from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.league import League


class LeagueRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, league_id: int) -> League | None:
        return self.db.get(League, league_id)

    def find_by_name_country(
        self,
        name: str,
        country: str | None,
    ) -> League | None:
        stmt = select(League).where(League.name == name)

        if country is None:
            stmt = stmt.where(League.country.is_(None))
        else:
            stmt = stmt.where(League.country == country)

        return self.db.scalar(stmt)

    def create(
        self,
        name: str,
        country: str | None = None,
        level: int | None = None,
    ) -> League:
        league: League = League(
            name=name,
            country=country,
            level=level,
        )
        self.db.add(league)
        self.db.flush()
        self.db.refresh(league)
        return league

    def get_or_create(
        self,
        name: str,
        country: str | None = None,
        level: int | None = None,
    ) -> League:
        league: League | None = self.find_by_name_country(name=name, country=country)
        if league is not None:
            return league
        return self.create(name=name, country=country, level=level)

    def list_all(self) -> list[League]:
        stmt = select(League).order_by(League.name.asc())
        return list(self.db.scalars(stmt).all())