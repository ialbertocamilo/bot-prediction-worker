from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.team import Team


class TeamRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, team_id: int) -> Team | None:
        return self.db.get(Team, team_id)

    def find_by_name_country(
        self,
        name: str,
        country: str | None = None,
    ) -> Team | None:
        stmt = select(Team).where(Team.name == name)

        if country is None:
            return self.db.scalar(stmt)

        stmt = stmt.where(Team.country == country)
        return self.db.scalar(stmt)

    def create(
        self,
        name: str,
        short_name: str | None = None,
        country: str | None = None,
        founded_year: int | None = None,
    ) -> Team:
        team: Team = Team(
            name=name,
            short_name=short_name,
            country=country,
            founded_year=founded_year,
        )
        self.db.add(team)
        self.db.flush()
        self.db.refresh(team)
        return team

    def get_or_create(
        self,
        name: str,
        short_name: str | None = None,
        country: str | None = None,
        founded_year: int | None = None,
    ) -> Team:
        team: Team | None = self.find_by_name_country(name=name, country=country)
        if team is not None:
            return team

        return self.create(
            name=name,
            short_name=short_name,
            country=country,
            founded_year=founded_year,
        )

    def list_all(self) -> list[Team]:
        stmt = select(Team).order_by(Team.name.asc())
        return list(self.db.scalars(stmt).all())