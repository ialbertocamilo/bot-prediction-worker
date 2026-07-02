from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.league_strength import LeagueStrength


class LeagueStrengthRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_key(self, league_key: str) -> LeagueStrength | None:
        stmt = select(LeagueStrength).where(LeagueStrength.league_key == league_key)
        return self.db.scalar(stmt)

    def get_coefficient(self, league_key: str, default: float = 1.0) -> float:
        rec = self.get_by_key(league_key)
        return float(rec.coefficient) if rec is not None else default

    def upsert(
        self,
        *,
        league_key: str,
        coefficient: float,
        sample_size: int,
    ) -> LeagueStrength:
        existing = self.get_by_key(league_key)
        if existing is not None:
            existing.coefficient = coefficient
            existing.sample_size = sample_size
            self.db.flush()
            return existing

        rec = LeagueStrength(
            league_key=league_key,
            coefficient=coefficient,
            sample_size=sample_size,
        )
        self.db.add(rec)
        self.db.flush()
        return rec
