from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, and_
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
        attack: float | None = None,
        defense: float | None = None,
    ) -> TeamRating:
        team_rating: TeamRating = TeamRating(
            model_id=model_id,
            season_id=season_id,
            team_id=team_id,
            rating=rating,
            attack=attack,
            defense=defense,
            as_of_date=as_of_date,
            as_of_match_id=as_of_match_id,
        )
        self.db.add(team_rating)
        self.db.flush()
        self.db.refresh(team_rating)
        return team_rating

    def upsert_by_match(
        self,
        model_id: int,
        team_id: int,
        as_of_match_id: int,
        rating: float,
        as_of_date: datetime,
        attack: float | None = None,
        defense: float | None = None,
        season_id: int | None = None,
    ) -> TeamRating:
        """Create or update a rating for a (model, team, match) triple."""
        stmt = select(TeamRating).where(
            and_(
                TeamRating.model_id == model_id,
                TeamRating.team_id == team_id,
                TeamRating.as_of_match_id == as_of_match_id,
            )
        )
        existing = self.db.scalars(stmt).first()
        if existing is not None:
            existing.rating = rating
            existing.attack = attack
            existing.defense = defense
            existing.as_of_date = as_of_date
            self.db.flush()
            return existing
        return self.create(
            model_id=model_id,
            team_id=team_id,
            rating=rating,
            as_of_date=as_of_date,
            season_id=season_id,
            as_of_match_id=as_of_match_id,
            attack=attack,
            defense=defense,
        )

    def exists_for_match(self, model_id: int, as_of_match_id: int) -> bool:
        """Check if any ratings exist for a given (model, match)."""
        stmt = select(TeamRating.id).where(
            and_(
                TeamRating.model_id == model_id,
                TeamRating.as_of_match_id == as_of_match_id,
            )
        ).limit(1)
        return self.db.scalars(stmt).first() is not None

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