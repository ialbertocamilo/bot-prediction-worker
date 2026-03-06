from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.raw_record import RawRecord


class RawRecordRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def create(
        self,
        source_id: int,
        entity_type: str,
        payload: dict,
        external_id: str | None = None,
        fetched_at: datetime | None = None,
    ) -> RawRecord:
        raw_record: RawRecord = RawRecord(
            source_id=source_id,
            entity_type=entity_type,
            external_id=external_id,
            fetched_at=fetched_at or datetime.utcnow(),
            payload=payload,
        )
        self.db.add(raw_record)
        self.db.flush()
        self.db.refresh(raw_record)
        return raw_record

    def list_by_source_and_entity(
        self,
        source_id: int,
        entity_type: str,
    ) -> list[RawRecord]:
        stmt = (
            select(RawRecord)
            .where(RawRecord.source_id == source_id)
            .where(RawRecord.entity_type == entity_type)
            .order_by(RawRecord.fetched_at.desc())
        )
        return list(self.db.scalars(stmt).all())