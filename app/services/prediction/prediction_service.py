"""
Prediction service — fits Dixon-Coles on DB data and stores results.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.match_feature_repository import MatchFeatureRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.repositories.prediction.team_rating_repository import TeamRatingRepository
from app.services.prediction.dixon_coles import DixonColesModel, DixonColesParams, MatchData

logger = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles_v1"
MODEL_DESCRIPTION = "Dixon-Coles (1997) con corrección ρ y decaimiento temporal"
TIME_DECAY = 0.005
MIN_MATCHES = 30


class PredictionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.match_repo = MatchRepository(db)
        self.model_repo = ModelRepository(db)
        self.prediction_repo = PredictionRepository(db)
        self.feature_repo = MatchFeatureRepository(db)
        self.rating_repo = TeamRatingRepository(db)

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

        now_ts = datetime.now(timezone.utc)
        match_data: list[MatchData] = []
        for m in training:
            if m.home_goals is None or m.away_goals is None:
                continue
            days_ago = 0.0
            if m.utc_date:
                delta = (now_ts - m.utc_date).total_seconds() / 86400.0
                days_ago = max(delta, 0.0)
            w = math.exp(-TIME_DECAY * days_ago)
            match_data.append(MatchData(
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                home_goals=m.home_goals,
                away_goals=m.away_goals,
                weight=w,
            ))

        if len(match_data) < MIN_MATCHES:
            return None

        dc = DixonColesModel(time_decay=TIME_DECAY)
        params = dc.fit(match_data)
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
            self.rating_repo.create(
                model_id=model_rec.id,
                team_id=tid,
                rating=att - dfn,
                as_of_date=as_of,
                as_of_match_id=match_id,
            )

        prediction = self.prediction_repo.create(
            match_id=match_id,
            model_id=model_rec.id,
            p_home=result["p_home"],
            p_draw=result["p_draw"],
            p_away=result["p_away"],
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
            data_quality=f"{len(match_data)}_matches",
        )

        self.db.commit()
        return self._to_dict(prediction, match)

    def _training_matches(self, league_id: int, exclude_id: int) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.league_id == league_id)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .where(Match.id != exclude_id)
            .order_by(Match.utc_date.asc())
        )
        return list(self.db.scalars(stmt).all())

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
