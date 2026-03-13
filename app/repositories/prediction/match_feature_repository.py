from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.match_feature import MatchFeature


class MatchFeatureRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_match_and_model(
        self,
        match_id: int,
        model_id: int,
    ) -> MatchFeature | None:
        stmt = (
            select(MatchFeature)
            .where(MatchFeature.match_id == match_id)
            .where(MatchFeature.model_id == model_id)
        )
        return self.db.scalar(stmt)

    def create(
        self,
        match_id: int,
        model_id: int,
        lambda_home: float | None = None,
        lambda_away: float | None = None,
        rating_home: float | None = None,
        rating_away: float | None = None,
        rating_diff: float | None = None,
        home_goals_for_avg: float | None = None,
        home_goals_against_avg: float | None = None,
        away_goals_for_avg: float | None = None,
        away_goals_against_avg: float | None = None,
        features_hash: str | None = None,
    ) -> MatchFeature:
        feature: MatchFeature = MatchFeature(
            match_id=match_id,
            model_id=model_id,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            rating_home=rating_home,
            rating_away=rating_away,
            rating_diff=rating_diff,
            home_goals_for_avg=home_goals_for_avg,
            home_goals_against_avg=home_goals_against_avg,
            away_goals_for_avg=away_goals_for_avg,
            away_goals_against_avg=away_goals_against_avg,
            features_hash=features_hash,
        )
        self.db.add(feature)
        self.db.flush()
        self.db.refresh(feature)
        return feature

    def update(
        self,
        feature: MatchFeature,
        *,
        lambda_home: float | None = None,
        lambda_away: float | None = None,
        rating_home: float | None = None,
        rating_away: float | None = None,
        rating_diff: float | None = None,
        home_goals_for_avg: float | None = None,
        home_goals_against_avg: float | None = None,
        away_goals_for_avg: float | None = None,
        away_goals_against_avg: float | None = None,
        features_hash: str | None = None,
    ) -> MatchFeature:
        _updates: dict[str, object] = {
            "lambda_home": lambda_home,
            "lambda_away": lambda_away,
            "rating_home": rating_home,
            "rating_away": rating_away,
            "rating_diff": rating_diff,
            "home_goals_for_avg": home_goals_for_avg,
            "home_goals_against_avg": home_goals_against_avg,
            "away_goals_for_avg": away_goals_for_avg,
            "away_goals_against_avg": away_goals_against_avg,
            "features_hash": features_hash,
        }
        for key, value in _updates.items():
            if value is not None:
                setattr(feature, key, value)

        self.db.flush()
        self.db.refresh(feature)
        return feature

    def upsert(
        self,
        match_id: int,
        model_id: int,
        lambda_home: float | None = None,
        lambda_away: float | None = None,
        rating_home: float | None = None,
        rating_away: float | None = None,
        rating_diff: float | None = None,
        home_goals_for_avg: float | None = None,
        home_goals_against_avg: float | None = None,
        away_goals_for_avg: float | None = None,
        away_goals_against_avg: float | None = None,
        features_hash: str | None = None,
    ) -> MatchFeature:
        existing: MatchFeature | None = self.get_by_match_and_model(
            match_id=match_id,
            model_id=model_id,
        )
        if existing is not None:
            return self.update(
                existing,
                lambda_home=lambda_home,
                lambda_away=lambda_away,
                rating_home=rating_home,
                rating_away=rating_away,
                rating_diff=rating_diff,
                home_goals_for_avg=home_goals_for_avg,
                home_goals_against_avg=home_goals_against_avg,
                away_goals_for_avg=away_goals_for_avg,
                away_goals_against_avg=away_goals_against_avg,
                features_hash=features_hash,
            )

        return self.create(
            match_id=match_id,
            model_id=model_id,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            rating_home=rating_home,
            rating_away=rating_away,
            rating_diff=rating_diff,
            home_goals_for_avg=home_goals_for_avg,
            home_goals_against_avg=home_goals_against_avg,
            away_goals_for_avg=away_goals_for_avg,
            away_goals_against_avg=away_goals_against_avg,
            features_hash=features_hash,
        )