from sqlalchemy import String, Integer, Date, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    date_of_birth: Mapped[object | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(80), nullable=True)
    position: Mapped[str] = mapped_column(String(30), nullable=False, default="UNKNOWN")
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    foot: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")

    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_value_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_until: Mapped[object | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team = relationship("Team", lazy="joined")

    __table_args__ = (
        Index("ix_players_name", "name"),
        Index("ix_players_team", "team_id"),
    )
