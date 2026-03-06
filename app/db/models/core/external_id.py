from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    func,
    UniqueConstraint,
    Index,
    Integer
)

from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ExternalId(Base):
    __tablename__ = "external_ids"

    id: Mapped[int] = mapped_column(primary_key=True)

    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False
    )

    entity_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    ) 

    external_id: Mapped[str] = mapped_column(
        String(120),
        nullable=False
    )

    canonical_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    source = relationship("Source", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "entity_type",
            "external_id",
            name="uq_external_ids_source_entity_ext"
        ),

        Index(
            "ix_external_ids_entity_canonical",
            "entity_type",
            "canonical_id"
        ),
    )