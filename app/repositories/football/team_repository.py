from __future__ import annotations

from difflib import SequenceMatcher

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

        # 3) SQL ilike — substring candidates, filtered by similarity
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

        name_lower = name.lower().strip()
        best: Team | None = None
        best_ratio: float = 0.0
        for candidate in self.db.scalars(stmt):
            ratio = SequenceMatcher(
                None, name_lower, candidate.name.lower().strip(),
            ).ratio()
            if ratio >= threshold and ratio > best_ratio:
                best = candidate
                best_ratio = ratio
        return best

    def create(
        self,
        name: str,
        short_name: str | None = None,
        country: str | None = None,
        founded_year: int | None = None,
        crest_url: str | None = None,
    ) -> Team:
        team: Team = Team(
            name=name,
            short_name=short_name,
            country=country,
            founded_year=founded_year,
            crest_url=crest_url,
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
        crest_url: str | None = None,
    ) -> Team:
        team: Team | None = self.find_by_name_country(name=name, country=country)
        if team is not None:
            # Update crest_url if missing and we have one now
            if crest_url and not team.crest_url:
                team.crest_url = crest_url
                self.db.flush()
            return team

        return self.create(
            name=name,
            short_name=short_name,
            country=country,
            founded_year=founded_year,
            crest_url=crest_url,
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