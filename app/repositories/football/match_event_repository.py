from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match_event import MatchEvent


class MatchEventRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, event_id: int) -> MatchEvent | None:
        return self.db.get(MatchEvent, event_id)

    def create(
        self,
        match_id: int,
        event_type: str,
        minute: int | None = None,
        extra_minute: int | None = None,
        team_id: int | None = None,
        player_name: str | None = None,
        assist_name: str | None = None,
        event_detail: str | None = None,
    ) -> MatchEvent:
        event: MatchEvent = MatchEvent(
            match_id=match_id,
            minute=minute,
            extra_minute=extra_minute,
            team_id=team_id,
            player_name=player_name,
            assist_name=assist_name,
            event_type=event_type,
            event_detail=event_detail,
        )
        self.db.add(event)
        self.db.flush()
        self.db.refresh(event)
        return event

    def list_by_match(self, match_id: int) -> list[MatchEvent]:
        stmt = (
            select(MatchEvent)
            .where(MatchEvent.match_id == match_id)
            .order_by(MatchEvent.minute.asc(), MatchEvent.id.asc())
        )
        return list(self.db.scalars(stmt).all())