from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.unlocked_match import UnlockedMatch


class UnlockedMatchRepository:
    """Check and register match-level entitlements per user."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def is_unlocked(self, telegram_id: int, match_id: int) -> bool:
        """Return True if the user already paid for this match."""
        stmt = (
            select(UnlockedMatch.id)
            .where(
                UnlockedMatch.telegram_id == telegram_id,
                UnlockedMatch.match_id == match_id,
            )
            .limit(1)
        )
        return self.db.scalar(stmt) is not None

    def unlock(self, telegram_id: int, match_id: int) -> None:
        """Record that the user has paid for this match."""
        row = UnlockedMatch(telegram_id=telegram_id, match_id=match_id)
        self.db.add(row)
        self.db.flush()
