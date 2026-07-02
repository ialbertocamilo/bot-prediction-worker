from sqlalchemy import DateTime, Integer, String, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DataQualityReport(Base):
    __tablename__ = "data_quality_report"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_dqr_created_at", "created_at"),
        Index("ix_dqr_issue_type_created_at", "issue_type", "created_at"),
    )
