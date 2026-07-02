from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.data_quality_report import DataQualityReport


class DataQualityReportRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def create(
        self,
        *,
        issue_type: str,
        entity_id: str | None,
        description: str,
    ) -> DataQualityReport:
        rec = DataQualityReport(
            issue_type=issue_type,
            entity_id=entity_id,
            description=description,
        )
        self.db.add(rec)
        self.db.flush()
        return rec

    def list_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 500,
        issue_type: str | None = None,
    ) -> list[DataQualityReport]:
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
        stmt = select(DataQualityReport).where(DataQualityReport.created_at >= since)
        if issue_type:
            stmt = stmt.where(DataQualityReport.issue_type == issue_type)
        stmt = stmt.order_by(DataQualityReport.created_at.desc(), DataQualityReport.id.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())
