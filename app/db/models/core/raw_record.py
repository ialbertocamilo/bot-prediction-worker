from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    func,
    Index
)

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RawRecord(Base):
    __tablename__ = "raw_records"

    id: Mapped[int] = mapped_column(primary_key=True)

    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False
    )

    entity_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    ) 

    external_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True
    )

    fetched_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False
    )

    source = relationship("Source", lazy="joined")

    __table_args__ = (
        Index(
            "ix_raw_records_source_entity_time",
            "source_id",
            "entity_type",
            "fetched_at"
        ),
    )