"""
Prediction service — fits Dixon-Coles on DB data and stores results.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
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
from app.services.prediction.calibration import PlattCalibrator
from config import HOME_ADVANTAGE, TIME_DECAY, XG_REG_WEIGHT, CALIBRATION_ENABLED, CALIBRATION_MIN_SAMPLES

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
        self._calibrator: PlattCalibrator | None = None

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

    def predict_match(self, match_id: int) -> dict | None:
        """
        Predict a match using Dixon-Coles fitted on the same league's
        historical data.  Persists features, ratings and prediction to the DB.
        Returns a dict ready for the API / bot, or *None*.
        """
        match = self.match_repo.get_by_id(match_id)
        if match is None:
            return None

        model_rec = self.model_repo.get_or_create(
            name=MODEL_NAME,
            description=MODEL_DESCRIPTION,
        )
    
        existing = self.prediction_repo.latest_for_match_and_model(
            match_id=match_id,
            model_id=model_rec.id,
        )
        if existing is not None:
            return self._to_dict(existing, match)

        training = self._training_matches(match.league_id, match.id)
        if len(training) < MIN_MATCHES:
            return None

        # Use the target match date as temporal reference (consistent with
        # backtesting/rolling retrain).  Fall back to now for safety.
        ref_ts = match.utc_date or datetime.now(timezone.utc)
        td, xg_w, ha = self._league_params(match.league_id)
        xg_map = self._load_xg_map([m.id for m in training])
        match_data: list[MatchData] = []
        # Collect per-team xG aggregates for regularization priors
        xg_for_lists: dict[int, list[float]] = {}
        xg_against_lists: dict[int, list[float]] = {}
        for m in training:
            if m.home_goals is None or m.away_goals is None:
                continue
            days_ago = 0.0
            if m.utc_date:
                delta = (ref_ts - m.utc_date).total_seconds() / 86400.0
                days_ago = max(delta, 0.0)
            w = math.exp(-td * days_ago)

            match_data.append(MatchData(
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                home_goals=m.home_goals,
                away_goals=m.away_goals,
                weight=w,
            ))

            # Accumulate xG per team (for = own offensive, against = opponent)
            pair = xg_map.get(m.id, {})
            h_xg = pair.get(m.home_team_id)
            a_xg = pair.get(m.away_team_id)
            if h_xg is not None and a_xg is not None:
                xg_for_lists.setdefault(m.home_team_id, []).append(h_xg)
                xg_against_lists.setdefault(m.home_team_id, []).append(a_xg)
                xg_for_lists.setdefault(m.away_team_id, []).append(a_xg)
                xg_against_lists.setdefault(m.away_team_id, []).append(h_xg)

        if len(match_data) < MIN_MATCHES:
            return None

        dc = DixonColesModel(time_decay=td, home_adv_init=ha)

        # Build xG priors: {team_id: (avg_xg_for, avg_xg_against)}
        xg_priors: dict[int, tuple[float, float]] = {}
        for tid in set(xg_for_lists) & set(xg_against_lists):
            avg_for = sum(xg_for_lists[tid]) / len(xg_for_lists[tid])
            avg_against = sum(xg_against_lists[tid]) / len(xg_against_lists[tid])
            xg_priors[tid] = (avg_for, avg_against)

        params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=xg_w)
        result = dc.predict_match(match.home_team_id, match.away_team_id, params)

        self.feature_repo.upsert(
            match_id=match_id,
            model_id=model_rec.id,
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
                model_id=model_rec.id,
                team_id=tid,
                as_of_match_id=match_id,
                rating=att - dfn,
                attack=att,
                defense=dfn,
                as_of_date=as_of,
            )

        # ── Platt calibration (optional post-processing) ──
        cal_home, cal_draw, cal_away = self._calibrate_1x2(
            result["p_home"], result["p_draw"], result["p_away"],
        )

        prediction = self.prediction_repo.create(
            match_id=match_id,
            model_id=model_rec.id,
            p_home=cal_home,
            p_draw=cal_draw,
            p_away=cal_away,
            p_over_1_5=result["p_over_1_5"],
            p_under_1_5=result["p_under_1_5"],
            p_over_2_5=result["p_over_2_5"],
            p_under_2_5=result["p_under_2_5"],
            p_over_3_5=result["p_over_3_5"],
            p_under_3_5=result["p_under_3_5"],
            p_btts_yes=result["p_btts_yes"],
            p_btts_no=result["p_btts_no"],
            xg_home=result["xg_home"],
            xg_away=result["xg_away"],
            top_scorelines=result["top_scorelines"],
            data_quality=f"{len(match_data)}_matches_{len(xg_priors)}_xg_teams",
        )

        # Flush only — caller (worker) is responsible for commit.
        self.db.flush()
        return self._to_dict(prediction, match)

    # ── Platt calibration helpers ──────────────────────────────────────

    def _build_calibrator(self) -> PlattCalibrator:
        """Train a PlattCalibrator from historical prediction_eval data.

        Builds three outcome-specific calibrators (home/draw/away) packed
        into a single instance trained on the *home-win* outcome for
        simplicity.  In practice, we calibrate each 1X2 leg independently
        then re-normalise.
        """
        if self._calibrator is not None:
            return self._calibrator

        calibrator = PlattCalibrator()
        if not CALIBRATION_ENABLED:
            self._calibrator = calibrator
            return calibrator

        # Gather evaluated predictions: raw model prob vs actual binary outcome
        stmt = (
            select(Prediction.p_home, Prediction.p_draw, Prediction.p_away,
                   PredictionEval.actual_outcome)
            .join(PredictionEval, PredictionEval.prediction_id == Prediction.id)
        )
        rows = list(self.db.execute(stmt))
        if len(rows) < CALIBRATION_MIN_SAMPLES:
            logger.info(
                "Calibration skipped: %d evaluated predictions < %d minimum",
                len(rows), CALIBRATION_MIN_SAMPLES,
            )
            self._calibrator = calibrator
            return calibrator

        # Train on home-win probabilities (most data-efficient single calibrator)
        import numpy as np
        predicted = np.array([r.p_home for r in rows], dtype=np.float64)
        actual = np.array([1.0 if r.actual_outcome == "HOME" else 0.0 for r in rows], dtype=np.float64)
        calibrator.fit(predicted, actual)
        self._calibrator = calibrator
        return calibrator

    def _calibrate_1x2(
        self,
        p_home: float, p_draw: float, p_away: float,
    ) -> tuple[float, float, float]:
        """Calibrate 1X2 probabilities using Platt scaling and re-normalise.

        Returns calibrated (home, draw, away) that sum to 1.0.
        If calibrator is not fitted the raw probabilities are returned unchanged.
        """
        cal = self._build_calibrator()
        if not cal.is_fitted:
            return p_home, p_draw, p_away

        # Calibrate each leg independently
        c_home = cal.transform(p_home)
        c_draw = cal.transform(p_draw)
        c_away = cal.transform(p_away)

        # Re-normalise to sum to 1.0
        total = c_home + c_draw + c_away
        if total <= 0:
            return p_home, p_draw, p_away

        c_home /= total
        c_draw /= total
        c_away /= total

        logger.debug(
            "Calibrated 1X2: (%.4f,%.4f,%.4f) → (%.4f,%.4f,%.4f)",
            p_home, p_draw, p_away, c_home, c_draw, c_away,
        )
        return round(c_home, 6), round(c_draw, 6), round(c_away, 6)

    def _load_xg_map(self, match_ids: list[int]) -> dict[int, dict[int, float]]:
        """Load xG values from match_stats for given match IDs.

        Returns ``{match_id: {team_id: xg}}``.
        """
        if not match_ids:
            return {}
        result: dict[int, dict[int, float]] = {}
        batch_size = 500
        for start in range(0, len(match_ids), batch_size):
            batch = match_ids[start: start + batch_size]
            stmt = (
                select(MatchStats.match_id, MatchStats.team_id, MatchStats.xg)
                .where(MatchStats.match_id.in_(batch))
                .where(MatchStats.xg.isnot(None))
            )
            for row in self.db.execute(stmt):
                result.setdefault(row.match_id, {})[row.team_id] = row.xg
        return result

    def _training_matches(self, league_id: int, exclude_id: int) -> list[Match]:
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
        return list(self.db.scalars(stmt).all())

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
    def _to_dict(pred, match: Match) -> dict:
        d = {
            "match_id": match.id,
            "home_team": match.home_team.name if match.home_team else "?",
            "away_team": match.away_team.name if match.away_team else "?",
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "league": match.league.name if match.league else "?",
            "utc_date": match.utc_date,
            "status": match.status,
            "p_home": pred.p_home,
            "p_draw": pred.p_draw,
            "p_away": pred.p_away,
            "p_over_1_5": pred.p_over_1_5,
            "p_under_1_5": pred.p_under_1_5,
            "p_over_2_5": pred.p_over_2_5,
            "p_under_2_5": pred.p_under_2_5,
            "p_over_3_5": pred.p_over_3_5,
            "p_under_3_5": pred.p_under_3_5,
            "p_btts_yes": pred.p_btts_yes,
            "p_btts_no": pred.p_btts_no,
            "xg_home": pred.xg_home,
            "xg_away": pred.xg_away,
            "top_scorelines": pred.top_scorelines,
            "model": MODEL_NAME,
            "data_quality": pred.data_quality,
            # Double chance — derived from 1X2
            "p_1x": round(pred.p_home + pred.p_draw, 4),
            "p_x2": round(pred.p_draw + pred.p_away, 4),
            "p_12": round(pred.p_home + pred.p_away, 4),
        }
        return d
