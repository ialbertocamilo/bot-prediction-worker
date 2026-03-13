"""
Odds ingest service — normalises CanonicalOdds into market_odds rows.

Provider-agnostic: works with any data source that produces CanonicalOdds.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.external_id import ExternalId
from app.domain.canonical import CanonicalOdds
from app.repositories.prediction.market_odds_repository import MarketOddsRepository

logger = logging.getLogger(__name__)


class OddsIngestService:
    def __init__(self, db: Session) -> None:
        self.db: Session = db
        self.odds_repo = MarketOddsRepository(db)

    def ingest_odds(
        self,
        odds_list: list[CanonicalOdds],
        source_match_id_to_db_id: dict[str, int] | None = None,
    ) -> list[int]:
        """Ingest a list of CanonicalOdds into market_odds.

        For each CanonicalOdds we expect three records with market="1X2"
        and selection in ("home", "draw", "away").  They are grouped by
        match_external_id and bookmaker, then stored as a single row.

        Returns list of created/updated market_odds IDs.
        """
        # Group by (match_external_id, bookmaker)
        grouped: dict[tuple[str, str], dict[str, float]] = {}
        timestamps: dict[tuple[str, str], datetime] = {}

        for odds in odds_list:
            if odds.market != "1X2":
                continue
            bk = odds.bookmaker or "unknown"
            key = (odds.match_external_id, bk)
            grouped.setdefault(key, {})
            grouped[key][odds.selection.lower()] = odds.odd
            timestamps[key] = odds.collected_at

        created_ids: list[int] = []
        mapping = source_match_id_to_db_id or {}

        for (ext_id, bookmaker), selections in grouped.items():
            home_odds = selections.get("home")
            draw_odds = selections.get("draw")
            away_odds = selections.get("away")
            if home_odds is None or draw_odds is None or away_odds is None:
                continue

            # Resolve match_id
            match_id = mapping.get(ext_id)
            if match_id is None:
                stmt = (
                    select(ExternalId.canonical_id)
                    .where(ExternalId.entity_type == "match")
                    .where(ExternalId.external_id == ext_id)
                )
                row = self.db.execute(stmt).first()
                if row is None:
                    continue
                match_id = row[0]

            rec = self.odds_repo.upsert(
                match_id=match_id,
                bookmaker=bookmaker,
                home_odds=home_odds,
                draw_odds=draw_odds,
                away_odds=away_odds,
                fetched_at=timestamps.get((ext_id, bookmaker)),
            )
            created_ids.append(rec.id)

        logger.info("Odds ingested: %d records from %d raw entries", len(created_ids), len(odds_list))
        return created_ids
