"""
Prediction service — fits Dixon-Coles on DB data and stores results.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

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
from app.services.prediction.calibration import MultiClassPlattCalibrator
from app.services.prediction.schemas import MatchPredictionResult
from config import HOME_ADVANTAGE, TIME_DECAY, XG_REG_WEIGHT, MIN_XG_MATCHES, CALIBRATION_ENABLED, CALIBRATION_MIN_SAMPLES

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
        self._calibrator: MultiClassPlattCalibrator | None = None

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

    def predict_match(self, match_id: int) -> MatchPredictionResult | None:
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
            return self._to_result(existing, match)

        # Use the target match date as temporal reference (consistent with
        # backtesting/rolling retrain).  Fall back to now for safety.
        ref_ts = match.utc_date or datetime.now(timezone.utc)

        training = self._training_matches(match.league_id, match.id, before_date=ref_ts)
        if len(training) < MIN_MATCHES:
            return None

        td, xg_w, ha = self._league_params(match.league_id)
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
            cal = self._build_calibrator()
            cal_home, cal_draw, cal_away = self._calibrate_1x2(
                result["p_home"], result["p_draw"], result["p_away"],
            )

            prediction = self.prediction_repo.create(
                match_id=match_id,
                model_id=model_id,
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
                data_quality=(
                    f"{len(match_data)}_matches_{len(xg_priors)}_xg_teams"
                    f"{'_calibrated_1x2' if cal.is_fitted else '_raw'}"
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

    def _build_calibrator(self) -> MultiClassPlattCalibrator:
        """Train a MultiClassPlattCalibrator from historical prediction_eval data.

        Builds three independent calibrators (home / draw / away) so each
        outcome class gets its own logistic mapping.
        """
        if self._calibrator is not None:
            return self._calibrator

        calibrator = MultiClassPlattCalibrator()
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

        import numpy as np
        p_home_arr = np.array([r.p_home for r in rows], dtype=np.float64)
        p_draw_arr = np.array([r.p_draw for r in rows], dtype=np.float64)
        p_away_arr = np.array([r.p_away for r in rows], dtype=np.float64)
        outcomes = np.array([r.actual_outcome for r in rows])

        calibrator.fit(p_home_arr, p_draw_arr, p_away_arr, outcomes)
        self._calibrator = calibrator
        return calibrator

    def _calibrate_1x2(
        self,
        p_home: float, p_draw: float, p_away: float,
    ) -> tuple[float, float, float]:
        """Calibrate 1X2 probabilities using per-class Platt scaling.

        Returns calibrated (home, draw, away) that sum to 1.0.
        If calibrator is not fitted the raw probabilities are returned unchanged.
        """
        cal = self._build_calibrator()
        if not cal.is_fitted:
            return p_home, p_draw, p_away

        c_home, c_draw, c_away = cal.calibrate_1x2(p_home, p_draw, p_away)

        logger.debug(
            "Calibrated 1X2: (%.4f,%.4f,%.4f) → (%.4f,%.4f,%.4f)",
            p_home, p_draw, p_away, c_home, c_draw, c_away,
        )
        return c_home, c_draw, c_away

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
