from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.team import Team

# Minimum similarity ratio to consider a fuzzy match
_FUZZY_THRESHOLD = 0.82


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
        threshold: float = _FUZZY_THRESHOLD,
    ) -> Team | None:
        """Find a team by exact name first, then fuzzy matching.

        Also checks the short_name column and substring containment.
        Returns None if no match meets the threshold.
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

        # 3) Fuzzy scan — load all teams once and score in memory
        all_teams = list(self.db.scalars(select(Team)).all())
        target = name.lower().strip()

        best_team: Team | None = None
        best_ratio: float = 0.0

        for t in all_teams:
            if country is not None and t.country and t.country != country:
                continue

            for candidate in (t.name, t.short_name):
                if candidate is None:
                    continue
                cand_lower = candidate.lower().strip()

                # Containment check (e.g. "Barcelona" in "FC Barcelona")
                # Only apply when the shorter side is at least 4 chars to
                # avoid false positives with very short names like "AC".
                shorter = min(len(target), len(cand_lower))
                if shorter >= 4 and (target in cand_lower or cand_lower in target):
                    return t

                ratio = SequenceMatcher(None, target, cand_lower).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_team = t

        if best_ratio >= threshold:
            return best_team
        return None

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