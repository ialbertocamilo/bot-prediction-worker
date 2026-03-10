from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match_stats import MatchStats


class MatchStatsRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, stats_id: int) -> MatchStats | None:
        return self.db.get(MatchStats, stats_id)

    def find_by_match_team(
        self,
        match_id: int,
        team_id: int,
    ) -> MatchStats | None:
        stmt = (
            select(MatchStats)
            .where(MatchStats.match_id == match_id)
            .where(MatchStats.team_id == team_id)
        )
        return self.db.scalar(stmt)

    def create(
        self,
        match_id: int,
        team_id: int,
        possession_pct: float | None = None,
        shots: int | None = None,
        shots_on_target: int | None = None,
        xg: float | None = None,
        xga: float | None = None,
        corners: int | None = None,
        fouls: int | None = None,
        offsides: int | None = None,
        yellow_cards: int | None = None,
        red_cards: int | None = None,
        passes: int | None = None,
        pass_accuracy_pct: float | None = None,
    ) -> MatchStats:
        stats = MatchStats(
            match_id=match_id,
            team_id=team_id,
            possession_pct=possession_pct,
            shots=shots,
            shots_on_target=shots_on_target,
            xg=xg,
            xga=xga,
            corners=corners,
            fouls=fouls,
            offsides=offsides,
            yellow_cards=yellow_cards,
            red_cards=red_cards,
            passes=passes,
            pass_accuracy_pct=pass_accuracy_pct,
        )
        self.db.add(stats)
        self.db.flush()
        self.db.refresh(stats)
        return stats

    def update(
        self,
        stats: MatchStats,
        **kwargs: object,
    ) -> MatchStats:
        for key, value in kwargs.items():
            if hasattr(stats, key):
                setattr(stats, key, value)
        self.db.flush()
        self.db.refresh(stats)
        return stats

    def upsert(
        self,
        match_id: int,
        team_id: int,
        **kwargs: object,
    ) -> MatchStats:
        existing = self.find_by_match_team(match_id=match_id, team_id=team_id)
        if existing is not None:
            return self.update(existing, **kwargs)
        return self.create(match_id=match_id, team_id=team_id, **kwargs)

    def list_by_match(self, match_id: int) -> list[MatchStats]:
        stmt = select(MatchStats).where(MatchStats.match_id == match_id)
        return list(self.db.scalars(stmt).all())
