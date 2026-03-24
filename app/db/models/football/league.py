from sqlalchemy import String, Integer, DateTime, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    last_ingest_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("name", "country", name="uq_leagues_name_country"),
    )