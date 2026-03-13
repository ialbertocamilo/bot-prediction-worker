from __future__ import annotations

from sqlalchemy import or_, select
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

    def find_by_name_fuzzy(
        self,
        name: str,
        country: str | None = None,
        threshold: float = 0.82,
    ) -> Team | None:
        """Find a team by exact name first, then SQL ilike fallback.

        Uses database-side pattern matching instead of loading all teams.
        Returns None if no match is found.
        """
        # 1) Exact match
        exact = self.find_by_name_country(name=name, country=country)
        if exact is not None:
            return exact

        # 2) Exact match on short_name
        stmt = select(Team).where(Team.short_name == name)
        if country is not None:
            stmt = stmt.where(Team.country == country)
        by_short = self.db.scalar(stmt)
        if by_short is not None:
            return by_short

        # 3) SQL ilike — substring search on name and short_name
        pattern = f"%{name}%"
        stmt = select(Team).where(
            or_(
                Team.name.ilike(pattern),
                Team.short_name.ilike(pattern),
            )
        )
        if country is not None:
            stmt = stmt.where(Team.country == country)
        stmt = stmt.limit(10)

        match = self.db.scalar(stmt)
        return match

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

    def search_by_name(self, query: str, limit: int = 20) -> list[Team]:
        pattern = f"%{query}%"
        stmt = (
            select(Team)
            .where(
                or_(
                    Team.name.ilike(pattern),
                    Team.short_name.ilike(pattern),
                )
            )
            .order_by(Team.name.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def list_all(self) -> list[Team]:
        stmt = select(Team).order_by(Team.name.asc())
        return list(self.db.scalars(stmt).all())