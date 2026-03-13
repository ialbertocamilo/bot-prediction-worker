from sqlalchemy import Float, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LeagueHyperparams(Base):
    """Per-league hyperparameters for Dixon-Coles model."""

    __tablename__ = "league_hyperparams"

    id: Mapped[int] = mapped_column(primary_key=True)

    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False,
    )

    time_decay: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_reg_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_advantage: Mapped[float | None] = mapped_column(Float, nullable=True)

    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)

    league = relationship("League", lazy="joined")

    __table_args__ = (
        UniqueConstraint("league_id", name="uq_league_hyperparams_league"),
    )
