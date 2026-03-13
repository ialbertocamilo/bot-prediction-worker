from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db.models.prediction.market_odds import MarketOdds


class MarketOddsRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def upsert(
        self,
        match_id: int,
        bookmaker: str,
        home_odds: float,
        draw_odds: float,
        away_odds: float,
        fetched_at: datetime | None = None,
    ) -> MarketOdds:
        """Insert or update odds for a match/bookmaker pair."""
        existing = self.get_by_match_and_bookmaker(match_id, bookmaker)
        if existing is not None:
            existing.home_odds = home_odds
            existing.draw_odds = draw_odds
            existing.away_odds = away_odds
            existing.fetched_at = fetched_at or datetime.now(timezone.utc)
            self.db.flush()
            return existing

        odds = MarketOdds(
            match_id=match_id,
            bookmaker=bookmaker,
            home_odds=home_odds,
            draw_odds=draw_odds,
            away_odds=away_odds,
            fetched_at=fetched_at or datetime.now(timezone.utc),
        )
        self.db.add(odds)
        self.db.flush()
        self.db.refresh(odds)
        return odds

    def get_by_match_and_bookmaker(
        self, match_id: int, bookmaker: str,
    ) -> MarketOdds | None:
        stmt = (
            select(MarketOdds)
            .where(and_(
                MarketOdds.match_id == match_id,
                MarketOdds.bookmaker == bookmaker,
            ))
        )
        return self.db.scalar(stmt)

    def list_by_match(self, match_id: int) -> list[MarketOdds]:
        stmt = (
            select(MarketOdds)
            .where(MarketOdds.match_id == match_id)
            .order_by(MarketOdds.fetched_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def latest_for_match(self, match_id: int) -> MarketOdds | None:
        """Return the most recently fetched odds for a match (any bookmaker)."""
        stmt = (
            select(MarketOdds)
            .where(MarketOdds.match_id == match_id)
            .order_by(MarketOdds.fetched_at.desc())
        )
        return self.db.scalars(stmt).first()

    def consensus_for_match(self, match_id: int) -> dict[str, float] | None:
        """Average odds across all bookmakers for a match.

        Returns ``{home_odds, draw_odds, away_odds}`` or None.
        """
        rows = self.list_by_match(match_id)
        if not rows:
            return None
        n = len(rows)
        avg_home = sum(r.home_odds for r in rows) / n
        avg_draw = sum(r.draw_odds for r in rows) / n
        avg_away = sum(r.away_odds for r in rows) / n
        return {
            "home_odds": round(avg_home, 3),
            "draw_odds": round(avg_draw, 3),
            "away_odds": round(avg_away, 3),
            "bookmakers": n,
        }
