from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.league_hyperparams import LeagueHyperparams


class LeagueHyperparamsRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_league(self, league_id: int) -> LeagueHyperparams | None:
        stmt = select(LeagueHyperparams).where(
            LeagueHyperparams.league_id == league_id,
        )
        return self.db.scalar(stmt)

    def upsert(
        self,
        league_id: int,
        *,
        time_decay: float | None = None,
        xg_reg_weight: float | None = None,
        home_advantage: float | None = None,
        notes: str | None = None,
    ) -> LeagueHyperparams:
        existing = self.get_by_league(league_id)
        if existing is not None:
            if time_decay is not None:
                existing.time_decay = time_decay
            if xg_reg_weight is not None:
                existing.xg_reg_weight = xg_reg_weight
            if home_advantage is not None:
                existing.home_advantage = home_advantage
            if notes is not None:
                existing.notes = notes
            self.db.flush()
            return existing
        hp = LeagueHyperparams(
            league_id=league_id,
            time_decay=time_decay,
            xg_reg_weight=xg_reg_weight,
            home_advantage=home_advantage,
            notes=notes,
        )
        self.db.add(hp)
        self.db.flush()
        self.db.refresh(hp)
        return hp

    def list_all(self) -> list[LeagueHyperparams]:
        stmt = select(LeagueHyperparams).order_by(LeagueHyperparams.league_id)
        return list(self.db.scalars(stmt).all())
