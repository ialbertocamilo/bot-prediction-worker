"""
Prediction service — fits Dixon-Coles on DB data and stores results.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, noload

from app.db.models.football.match import Match
from app.db.models.prediction.prediction import Prediction
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.league_hyperparams_repository import LeagueHyperparamsRepository
from app.repositories.prediction.match_feature_repository import MatchFeatureRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.repositories.prediction.team_rating_repository import TeamRatingRepository
from app.db.models.prediction.prediction_eval import PredictionEval
from app.repositories.prediction.prediction_eval_repository import PredictionEvalRepository
from app.services.prediction.dixon_coles import DixonColesModel, DixonColesParams, MatchData
from app.services.prediction.calibration import MultiClassPlattCalibrator, BinaryPlattCalibrator
from app.services.prediction.schemas import MatchPredictionResult
from app.services.canonical_league_service import domestic_key_for_league_name, strength_coefficient_for_key
from config import HOME_ADVANTAGE, TIME_DECAY, XG_REG_WEIGHT, MIN_XG_MATCHES, CALIBRATION_ENABLED, CALIBRATION_MIN_SAMPLES, TRAINING_WINDOW_DAYS

logger = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles_v1"
MODEL_DESCRIPTION = "Dixon-Coles (1997) con corrección ρ y decaimiento temporal"
MIN_MATCHES = 30
MAX_ATTACK_DEFENSE = 5.0


class PredictionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.match_repo = MatchRepository(db)
        self.model_repo = ModelRepository(db)
        self.prediction_repo = PredictionRepository(db)
        self.feature_repo = MatchFeatureRepository(db)
        self.rating_repo = TeamRatingRepository(db)
        self.hp_repo = LeagueHyperparamsRepository(db)
        self.eval_repo = PredictionEvalRepository(db)
        self._calibrators: dict[int | None, MultiClassPlattCalibrator] = {}
        self._ou25_calibrators: dict[int | None, BinaryPlattCalibrator] = {}
        self._btts_calibrators: dict[int | None, BinaryPlattCalibrator] = {}

    def _league_params(
        self, league_id: int,
    ) -> tuple[float, float, float]:
        """Return (time_decay, xg_reg_weight, home_advantage) for a league.

        Uses per-league overrides from league_hyperparams table if available,
        falling back to global config values.
        """
        hp = self.hp_repo.get_by_league(league_id)
        td = hp.time_decay if hp and hp.time_decay is not None else TIME_DECAY
        xg_w = hp.xg_reg_weight if hp and hp.xg_reg_weight is not None else XG_REG_WEIGHT
        ha = hp.home_advantage if hp and hp.home_advantage is not None else HOME_ADVANTAGE
        return td, xg_w, ha

    def predict_match(self, match_id: int, *, force: bool = False) -> MatchPredictionResult | None:
        """
        Predict a match using Dixon-Coles fitted on the same league's
        historical data.  Persists features, ratings and prediction to the DB.
        Returns a dict ready for the API / bot, or *None*.
        """
        match = self.match_repo.get_by_id(match_id)
        if match is None:
            return None

        # ── Filtro de Fantasmas ───────────────────────────────────────────
        # Equipos sin domestic_league_key = datos insuficientes para predecir.
        home_key = getattr(match.home_team, "domestic_league_key", None)
        away_key = getattr(match.away_team, "domestic_league_key", None)
        if not home_key or not away_key:
            logger.info(
                "Ghost filter: match %d skipped — missing domestic_league_key "
                "(home=%s [%s], away=%s [%s])",
                match_id,
                match.home_team.name if match.home_team else "?", home_key,
                match.away_team.name if match.away_team else "?", away_key,
            )
            return None

        # ── Enrutador doméstico / internacional ───────────────────────────
        # Determina si el partido se juega en una liga doméstica de ambos
        # equipos o en un torneo internacional.
        match_league_key = domestic_key_for_league_name(
            match.league.name if match.league else "",
        )
        is_domestic = match_league_key is not None and (
            match_league_key == home_key or match_league_key == away_key
        )
        if not is_domestic:
            return self._predict_cross_league(match, home_key, away_key, force=force)

        model_rec = self.model_repo.get_or_create(
            name=MODEL_NAME,
            description=MODEL_DESCRIPTION,
        )
    
        existing = self.prediction_repo.latest_for_match_and_model(
            match_id=match_id,
            model_id=model_rec.id,
        )
        if existing is not None and not force:
            return self._to_result(existing, match)

        # Use the target match date as temporal reference (consistent with
        # backtesting/rolling retrain).  Fall back to now for safety.
        ref_ts = match.utc_date or datetime.now(timezone.utc)

        # ── Resolve sibling league_ids (season-split fix) ─────────────────
        # match_league_key is already resolved above (line ~95).  Use it to
        # fetch ALL league_ids that belong to the same canonical competition
        # so training data spans across season boundaries.
        sibling_ids = self._resolve_league_ids_for_key(match_league_key)
        if not sibling_ids:
            sibling_ids = [match.league_id]

        training = self._training_matches_multi(sibling_ids, match.id, before_date=ref_ts)
        if len(training) < MIN_MATCHES:
            return None

        td, xg_w, ha = self._league_params(sibling_ids[0])
        xg_map = self._load_xg_map([m.id for m in training])

        from app.services.prediction.training_data import build_training_data
        match_data, xg_priors = build_training_data(
            training, ref_ts, td, xg_map, MIN_XG_MATCHES,
        )

        if len(match_data) < MIN_MATCHES:
            return None

        dc = DixonColesModel(time_decay=td, home_adv_init=ha)
        params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=xg_w)
        result = dc.predict_match(match.home_team_id, match.away_team_id, params)

        model_id = model_rec.id
        try:
            self.feature_repo.upsert(
                match_id=match_id,
                model_id=model_id,
                lambda_home=result["lambda_home"],
                lambda_away=result["lambda_away"],
                rating_home=result["attack_home"],
                rating_away=result["attack_away"],
                rating_diff=result["attack_home"] - result["attack_away"],
                home_goals_for_avg=result["xg_home"],
                home_goals_against_avg=None,
                away_goals_for_avg=result["xg_away"],
                away_goals_against_avg=None,
            )

            as_of = datetime.now(timezone.utc)
            for tid in (match.home_team_id, match.away_team_id):
                att = params.attack.get(tid, 0.0)
                dfn = params.defense.get(tid, 0.0)
                att = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, att))
                dfn = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, dfn))
                self.rating_repo.upsert_by_match(
                    model_id=model_id,
                    team_id=tid,
                    as_of_match_id=match_id,
                    rating=att - dfn,
                    attack=att,
                    defense=dfn,
                    as_of_date=as_of,
                )

            # ── Platt calibration (optional post-processing) ──
            cal = self._build_calibrator(league_id=match.league_id)
            cal_home, cal_draw, cal_away = self._calibrate_1x2(
                result["p_home"], result["p_draw"], result["p_away"],
                league_id=match.league_id,
            )

            # Calibrate Over/Under 2.5 and BTTS via binary Platt scaling
            cal_over_25, cal_under_25 = self._calibrate_ou25(
                result["p_over_2_5"], result["p_under_2_5"],
                league_id=match.league_id,
            )
            cal_btts_yes, cal_btts_no = self._calibrate_btts(
                result["p_btts_yes"], result["p_btts_no"],
                league_id=match.league_id,
            )

            prediction = self.prediction_repo.create(
                match_id=match_id,
                model_id=model_id,
                p_home=cal_home,
                p_draw=cal_draw,
                p_away=cal_away,
                p_over_1_5=result["p_over_1_5"],
                p_under_1_5=result["p_under_1_5"],
                p_over_2_5=cal_over_25,
                p_under_2_5=cal_under_25,
                p_over_3_5=result["p_over_3_5"],
                p_under_3_5=result["p_under_3_5"],
                p_btts_yes=cal_btts_yes,
                p_btts_no=cal_btts_no,
                xg_home=result["xg_home"],
                xg_away=result["xg_away"],
                top_scorelines=result["top_scorelines"],
                data_quality=(
                    f"{len(match_data)}_matches_{len(xg_priors)}_xg_teams"
                    f"{'_calibrated' if cal.is_fitted else '_raw'}"
                ),
            )

            self.db.flush()
            return self._to_result(prediction, match)
        except IntegrityError:
            self.db.rollback()
            match = self.match_repo.get_by_id(match_id)
            if match is None:
                return None
            existing = self.prediction_repo.latest_for_match_and_model(
                match_id, model_id,
            )
            if existing is not None:
                return self._to_result(existing, match)
            return None

    # ── Platt calibration helpers ──────────────────────────────────────

    def _build_calibrator(self, league_id: int | None = None) -> MultiClassPlattCalibrator:
        """Train a MultiClassPlattCalibrator from historical prediction_eval data.

        Tries per-league calibration first.  Falls back to global (all leagues)
        when the league has fewer than CALIBRATION_MIN_SAMPLES evaluated predictions.
        """
        if league_id in self._calibrators:
            return self._calibrators[league_id]

        calibrator = MultiClassPlattCalibrator()
        if not CALIBRATION_ENABLED:
            self._calibrators[league_id] = calibrator
            return calibrator

        import numpy as np

        # Try per-league first
        rows = self._fetch_eval_rows(league_id=league_id)
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            logger.info(
                "Per-league calibration skipped (league=%s, %d < %d), trying global fallback",
                league_id, len(rows), CALIBRATION_MIN_SAMPLES,
            )
            rows = self._fetch_eval_rows(league_id=None)

        if len(rows) < CALIBRATION_MIN_SAMPLES:
            logger.info(
                "Global calibration skipped: %d evaluated predictions < %d minimum",
                len(rows), CALIBRATION_MIN_SAMPLES,
            )
            self._calibrators[league_id] = calibrator
            return calibrator

        p_home_arr = np.array([r.p_home for r in rows], dtype=np.float64)
        p_draw_arr = np.array([r.p_draw for r in rows], dtype=np.float64)
        p_away_arr = np.array([r.p_away for r in rows], dtype=np.float64)
        outcomes = np.array([r.actual_outcome for r in rows])

        calibrator.fit(p_home_arr, p_draw_arr, p_away_arr, outcomes)
        self._calibrators[league_id] = calibrator
        return calibrator

    def _build_ou25_calibrator(self, league_id: int | None = None) -> BinaryPlattCalibrator:
        """Build a binary Platt calibrator for Over/Under 2.5 goals."""
        if league_id in self._ou25_calibrators:
            return self._ou25_calibrators[league_id]

        cal = BinaryPlattCalibrator()
        if not CALIBRATION_ENABLED:
            self._ou25_calibrators[league_id] = cal
            return cal

        import numpy as np

        rows = self._fetch_ou_btts_rows(league_id=league_id)
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            rows = self._fetch_ou_btts_rows(league_id=None)
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            self._ou25_calibrators[league_id] = cal
            return cal

        predicted = np.array([r.p_over_2_5 for r in rows if r.p_over_2_5 is not None], dtype=np.float64)
        actual = np.array([
            1.0 if (r.home_goals + r.away_goals) > 2 else 0.0
            for r in rows if r.p_over_2_5 is not None
        ], dtype=np.float64)

        if len(predicted) >= CALIBRATION_MIN_SAMPLES:
            cal.fit(predicted, actual)
        self._ou25_calibrators[league_id] = cal
        return cal

    def _build_btts_calibrator(self, league_id: int | None = None) -> BinaryPlattCalibrator:
        """Build a binary Platt calibrator for BTTS (Both Teams To Score)."""
        if league_id in self._btts_calibrators:
            return self._btts_calibrators[league_id]

        cal = BinaryPlattCalibrator()
        if not CALIBRATION_ENABLED:
            self._btts_calibrators[league_id] = cal
            return cal

        import numpy as np

        rows = self._fetch_ou_btts_rows(league_id=league_id)
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            rows = self._fetch_ou_btts_rows(league_id=None)
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            self._btts_calibrators[league_id] = cal
            return cal

        predicted = np.array([r.p_btts_yes for r in rows if r.p_btts_yes is not None], dtype=np.float64)
        actual = np.array([
            1.0 if (r.home_goals > 0 and r.away_goals > 0) else 0.0
            for r in rows if r.p_btts_yes is not None
        ], dtype=np.float64)

        if len(predicted) >= CALIBRATION_MIN_SAMPLES:
            cal.fit(predicted, actual)
        self._btts_calibrators[league_id] = cal
        return cal

    def _fetch_eval_rows(self, league_id: int | None = None, before_date: datetime | None = None) -> list:
        """Fetch evaluated prediction rows for 1X2 calibration, optionally per-league.

        When *before_date* is supplied, only predictions for matches played
        before that timestamp are included — prevents calibration data leakage
        during backtesting.
        """
        stmt = (
            select(Prediction.p_home, Prediction.p_draw, Prediction.p_away,
                   PredictionEval.actual_outcome)
            .join(PredictionEval, PredictionEval.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
        )
        if league_id is not None:
            stmt = stmt.where(Match.league_id == league_id)
        if before_date is not None:
            stmt = stmt.where(Match.utc_date < before_date)
        return list(self.db.execute(stmt))

    def _fetch_ou_btts_rows(self, league_id: int | None = None, before_date: datetime | None = None) -> list:
        """Fetch prediction + actual goals rows for O/U and BTTS calibration.

        When *before_date* is supplied, only matches played before that
        timestamp are included — prevents calibration data leakage.
        """
        stmt = (
            select(
                Prediction.p_over_2_5, Prediction.p_btts_yes,
                Match.home_goals, Match.away_goals,
            )
            .join(Match, Match.id == Prediction.match_id)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
        )
        if league_id is not None:
            stmt = stmt.where(Match.league_id == league_id)
        if before_date is not None:
            stmt = stmt.where(Match.utc_date < before_date)
        return list(self.db.execute(stmt))

    def _calibrate_1x2(
        self,
        p_home: float, p_draw: float, p_away: float,
        league_id: int | None = None,
    ) -> tuple[float, float, float]:
        """Calibrate 1X2 probabilities using per-league Platt scaling.

        Returns calibrated (home, draw, away) that sum to 1.0.
        If calibrator is not fitted the raw probabilities are returned unchanged.
        """
        cal = self._build_calibrator(league_id=league_id)
        if not cal.is_fitted:
            return p_home, p_draw, p_away

        c_home, c_draw, c_away = cal.calibrate_1x2(p_home, p_draw, p_away)

        logger.debug(
            "Calibrated 1X2 (league=%s): (%.4f,%.4f,%.4f) → (%.4f,%.4f,%.4f)",
            league_id, p_home, p_draw, p_away, c_home, c_draw, c_away,
        )
        return c_home, c_draw, c_away

    def _calibrate_ou25(
        self,
        p_over: float, p_under: float,
        league_id: int | None = None,
    ) -> tuple[float, float]:
        """Calibrate Over/Under 2.5 using binary Platt scaling."""
        cal = self._build_ou25_calibrator(league_id=league_id)
        return cal.calibrate_pair(p_over, p_under)

    def _calibrate_btts(
        self,
        p_yes: float, p_no: float,
        league_id: int | None = None,
    ) -> tuple[float, float]:
        """Calibrate BTTS Yes/No using binary Platt scaling."""
        cal = self._build_btts_calibrator(league_id=league_id)
        return cal.calibrate_pair(p_yes, p_no)

    def _load_xg_map(self, match_ids: list[int]) -> dict[int, dict[int, float]]:
        """Load xG values from match_stats for given match IDs."""
        from app.services.prediction.training_data import load_xg_map
        return load_xg_map(self.db, match_ids)

    def _training_matches(
        self, league_id: int, exclude_id: int, before_date: datetime | None = None,
    ) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.league_id == league_id)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .where(Match.id != exclude_id)
            .order_by(Match.utc_date.asc())
            .options(noload("*"))
        )
        if before_date is not None:
            stmt = stmt.where(Match.utc_date < before_date)
            window_start = before_date - timedelta(days=TRAINING_WINDOW_DAYS)
            stmt = stmt.where(Match.utc_date >= window_start)
        return list(self.db.scalars(stmt).all())

    def _training_matches_multi(
        self, league_ids: list[int], exclude_id: int, before_date: datetime | None = None,
    ) -> list[Match]:
        """Fetch finished training matches across multiple league IDs."""
        if not league_ids:
            return []
        stmt = (
            select(Match)
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .where(Match.id != exclude_id)
            .order_by(Match.utc_date.asc())
            .options(noload("*"))
        )
        if before_date is not None:
            stmt = stmt.where(Match.utc_date < before_date)
            window_start = before_date - timedelta(days=TRAINING_WINDOW_DAYS)
            stmt = stmt.where(Match.utc_date >= window_start)
        return list(self.db.scalars(stmt).all())

    def _resolve_league_ids_for_key(self, canonical_key: str) -> list[int]:
        """Resolve a canonical league key to DB league_ids."""
        from app.services.canonical_league_service import CanonicalLeagueService
        svc = CanonicalLeagueService(self.db)
        return svc._resolved.get(canonical_key, [])

    def _fit_domestic_model(
        self,
        canonical_key: str,
        exclude_match_id: int,
        before_date: datetime | None,
    ) -> DixonColesParams | None:
        """Fit a Dixon-Coles model on a domestic league's historical data.

        Returns DixonColesParams or None if insufficient data.
        """
        league_ids = self._resolve_league_ids_for_key(canonical_key)
        if not league_ids:
            logger.info("Cross-league: no league_ids for key '%s'", canonical_key)
            return None

        # Use first league_id for hyperparams (they share the same canonical group)
        td, xg_w, ha = self._league_params(league_ids[0])

        training = self._training_matches_multi(league_ids, exclude_match_id, before_date)
        if len(training) < MIN_MATCHES:
            logger.info(
                "Cross-league: insufficient data for '%s' (%d < %d)",
                canonical_key, len(training), MIN_MATCHES,
            )
            return None

        xg_map = self._load_xg_map([m.id for m in training])

        from app.services.prediction.training_data import build_training_data
        match_data, xg_priors = build_training_data(
            training, before_date or datetime.now(timezone.utc), td, xg_map, MIN_XG_MATCHES,
        )

        if len(match_data) < MIN_MATCHES:
            return None

        dc = DixonColesModel(time_decay=td, home_adv_init=ha)
        return dc.fit(match_data, xg_priors=xg_priors, xg_weight=xg_w)

    def _predict_cross_league(
        self,
        match: Match,
        home_key: str,
        away_key: str,
        *,
        force: bool = False,
    ) -> MatchPredictionResult | None:
        """Cross-league prediction: assemble lambdas from two domestic models.

        Trains (or retrieves) the Dixon-Coles model for each team's domestic
        league, extracts attack/defense ratings, applies strength-coefficient
        adjustment, and produces probabilities via bivariate Poisson.
        """
        match_id = match.id
        ref_ts = match.utc_date or datetime.now(timezone.utc)

        model_rec = self.model_repo.get_or_create(
            name=MODEL_NAME,
            description=MODEL_DESCRIPTION,
        )

        # Cache check — return early if already predicted
        existing = self.prediction_repo.latest_for_match_and_model(
            match_id=match_id,
            model_id=model_rec.id,
        )
        if existing is not None and not force:
            return self._to_result(existing, match)

        # Fit domestic models for both teams
        params_home = self._fit_domestic_model(home_key, match_id, ref_ts)
        if params_home is None:
            logger.info("Cross-league: home model ('%s') failed for match %d", home_key, match_id)
            return None

        if away_key == home_key:
            params_away = params_home
        else:
            params_away = self._fit_domestic_model(away_key, match_id, ref_ts)
            if params_away is None:
                logger.info("Cross-league: away model ('%s') failed for match %d", away_key, match_id)
                return None

        # Extract team parameters from their respective domestic models
        avg_att_h = sum(params_home.attack.values()) / max(len(params_home.attack), 1)
        avg_def_h = sum(params_home.defense.values()) / max(len(params_home.defense), 1)
        avg_att_a = sum(params_away.attack.values()) / max(len(params_away.attack), 1)
        avg_def_a = sum(params_away.defense.values()) / max(len(params_away.defense), 1)

        atk_h = params_home.attack.get(match.home_team_id, avg_att_h)
        def_h = params_home.defense.get(match.home_team_id, avg_def_h)
        gamma = params_home.home_advantage

        atk_a = params_away.attack.get(match.away_team_id, avg_att_a)
        def_a = params_away.defense.get(match.away_team_id, avg_def_a)

        # Base lambdas (Dixon-Coles formula)
        lambda_home_base = math.exp(max(min(atk_h + def_a + gamma, 5), -20))
        lambda_away_base = math.exp(max(min(atk_a + def_h, 5), -20))

        # Strength coefficient adjustment
        c_h = strength_coefficient_for_key(home_key)
        c_a = strength_coefficient_for_key(away_key)
        ratio_h = c_h / c_a if c_a > 0 else 1.0
        ratio_a = c_a / c_h if c_h > 0 else 1.0

        lambda_home = lambda_home_base * ratio_h
        lambda_away = lambda_away_base * ratio_a

        # Average rho from both domestic models
        rho = (params_home.rho + params_away.rho) / 2.0

        logger.info(
            "Cross-league match %d: %s(%.2f) vs %s(%.2f) | "
            "λ_base=(%.2f,%.2f) → λ_adj=(%.2f,%.2f) | C=(%s:%.2f/%s:%.2f) ρ=%.3f",
            match_id,
            match.home_team.name if match.home_team else "?", atk_h,
            match.away_team.name if match.away_team else "?", atk_a,
            lambda_home_base, lambda_away_base,
            lambda_home, lambda_away,
            home_key, c_h, away_key, c_a, rho,
        )

        # Bivariate Poisson probabilities
        result = DixonColesModel.predict_from_lambdas(lambda_home, lambda_away, rho)

        # Persist
        model_id = model_rec.id
        try:
            self.feature_repo.upsert(
                match_id=match_id,
                model_id=model_id,
                lambda_home=result["lambda_home"],
                lambda_away=result["lambda_away"],
                rating_home=atk_h,
                rating_away=atk_a,
                rating_diff=atk_h - atk_a,
                home_goals_for_avg=result["xg_home"],
                home_goals_against_avg=None,
                away_goals_for_avg=result["xg_away"],
                away_goals_against_avg=None,
            )

            as_of = datetime.now(timezone.utc)
            for tid, att, dfn, p_model in [
                (match.home_team_id, atk_h, def_h, params_home),
                (match.away_team_id, atk_a, def_a, params_away),
            ]:
                att_c = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, att))
                dfn_c = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, dfn))
                self.rating_repo.upsert_by_match(
                    model_id=model_id,
                    team_id=tid,
                    as_of_match_id=match_id,
                    rating=att_c - dfn_c,
                    attack=att_c,
                    defense=dfn_c,
                    as_of_date=as_of,
                )

            n_home = len(params_home.teams)
            n_away = len(params_away.teams) if params_away is not params_home else n_home

            # ── Platt calibration (same as domestic flow) ──
            cal = self._build_calibrator(league_id=match.league_id)
            cal_home, cal_draw, cal_away = self._calibrate_1x2(
                result["p_home"], result["p_draw"], result["p_away"],
                league_id=match.league_id,
            )
            cal_over_25, cal_under_25 = self._calibrate_ou25(
                result["p_over_2_5"], result["p_under_2_5"],
                league_id=match.league_id,
            )
            cal_btts_yes, cal_btts_no = self._calibrate_btts(
                result["p_btts_yes"], result["p_btts_no"],
                league_id=match.league_id,
            )

            prediction = self.prediction_repo.create(
                match_id=match_id,
                model_id=model_id,
                p_home=cal_home,
                p_draw=cal_draw,
                p_away=cal_away,
                p_over_1_5=result["p_over_1_5"],
                p_under_1_5=result["p_under_1_5"],
                p_over_2_5=cal_over_25,
                p_under_2_5=cal_under_25,
                p_over_3_5=result["p_over_3_5"],
                p_under_3_5=result["p_under_3_5"],
                p_btts_yes=cal_btts_yes,
                p_btts_no=cal_btts_no,
                xg_home=result["xg_home"],
                xg_away=result["xg_away"],
                top_scorelines=result["top_scorelines"],
                data_quality=(
                    f"cross_league_{home_key}_{n_home}t_vs_{away_key}_{n_away}t"
                    f"{'_calibrated' if cal.is_fitted else '_raw'}"
                )[:100],
            )

            self.db.flush()
            return self._to_result(prediction, match)
        except IntegrityError:
            self.db.rollback()
            match = self.match_repo.get_by_id(match_id)
            if match is None:
                return None
            existing = self.prediction_repo.latest_for_match_and_model(
                match_id, model_id,
            )
            if existing is not None:
                return self._to_result(existing, match)
            return None

    def invalidate_stale_predictions(self) -> int:
        """Delete cached predictions for SCHEDULED matches so they get regenerated.

        Called after rolling retrain or optimize_model to ensure predictions
        reflect the latest model parameters.  Returns count of deleted rows.
        """
        from sqlalchemy import delete
        from app.db.models.prediction.prediction import Prediction

        subq = (
            select(Match.id)
            .where(Match.status == "SCHEDULED")
            .where(Match.utc_date >= datetime.now(timezone.utc))
            .scalar_subquery()
        )
        stmt = (
            delete(Prediction)
            .where(Prediction.match_id.in_(subq))
        )
        result = self.db.execute(stmt)
        count = result.rowcount
        if count:
            self.db.flush()
        return count

    def invalidate_league_predictions(self, league_id: int) -> int:
        """Delete cached predictions for future SCHEDULED matches in a league.

        Called when new finished results are ingested so that future
        predictions are regenerated with the updated training data.
        Historical predictions (for FINISHED matches) are preserved.
        Returns count of deleted rows.
        """
        from sqlalchemy import delete
        from app.db.models.prediction.prediction import Prediction

        subq = (
            select(Match.id)
            .where(Match.league_id == league_id)
            .where(Match.status.in_(("SCHEDULED", "NS")))
            .where(Match.utc_date >= datetime.now(timezone.utc))
            .scalar_subquery()
        )
        stmt = (
            delete(Prediction)
            .where(Prediction.match_id.in_(subq))
        )
        result = self.db.execute(stmt)
        count = result.rowcount
        if count:
            self.db.flush()
        return count

    @staticmethod
    def _to_result(pred, match: Match) -> MatchPredictionResult:
        return MatchPredictionResult(
            match_id=match.id,
            home_team=match.home_team.name if match.home_team else "?",
            away_team=match.away_team.name if match.away_team else "?",
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league=match.league.name if match.league else "?",
            utc_date=match.utc_date,
            status=match.status,
            p_home=pred.p_home,
            p_draw=pred.p_draw,
            p_away=pred.p_away,
            p_over_1_5=pred.p_over_1_5,
            p_under_1_5=pred.p_under_1_5,
            p_over_2_5=pred.p_over_2_5,
            p_under_2_5=pred.p_under_2_5,
            p_over_3_5=pred.p_over_3_5,
            p_under_3_5=pred.p_under_3_5,
            p_btts_yes=pred.p_btts_yes,
            p_btts_no=pred.p_btts_no,
            xg_home=pred.xg_home,
            xg_away=pred.xg_away,
            top_scorelines=pred.top_scorelines,
            model=MODEL_NAME,
            data_quality=pred.data_quality,
        )
