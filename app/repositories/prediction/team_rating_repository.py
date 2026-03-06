from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.team_rating import TeamRating


class TeamRatingRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def create(
        self,
        model_id: int,
        team_id: int,
        rating: float,
        as_of_date: datetime,
        season_id: int | None = None,
        as_of_match_id: int | None = None,
    ) -> TeamRating:
        team_rating: TeamRating = TeamRating(
            model_id=model_id,
            season_id=season_id,
            team_id=team_id,
            rating=rating,
            as_of_date=as_of_date,
            as_of_match_id=as_of_match_id,
        )
        self.db.add(team_rating)
        self.db.flush()
        self.db.refresh(team_rating)
        return team_rating

    def latest_for_team(
        self,
        model_id: int,
        team_id: int,
    ) -> TeamRating | None:
        stmt = (
            select(TeamRating)
            .where(TeamRating.model_id == model_id)
            .where(TeamRating.team_id == team_id)
            .order_by(TeamRating.as_of_date.desc(), TeamRating.id.desc())
        )
        return self.db.scalars(stmt).first()

    def list_for_team(
        self,
        model_id: int,
        team_id: int,
    ) -> list[TeamRating]:
        stmt = (
            select(TeamRating)
            .where(TeamRating.model_id == model_id)
            .where(TeamRating.team_id == team_id)
            .order_by(TeamRating.as_of_date.asc(), TeamRating.id.asc())
        )
        return list(self.db.scalars(stmt).all())