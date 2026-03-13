from sqlalchemy import Float, String, DateTime, ForeignKey, func, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MarketOdds(Base):
    __tablename__ = "market_odds"

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)

    home_odds: Mapped[float] = mapped_column(Float, nullable=False)
    draw_odds: Mapped[float] = mapped_column(Float, nullable=False)
    away_odds: Mapped[float] = mapped_column(Float, nullable=False)

    fetched_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    match = relationship("Match", lazy="select")

    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", name="uq_market_odds_match_bookmaker"),
        Index("ix_market_odds_match_id", "match_id"),
        Index("ix_market_odds_fetched_at", "fetched_at"),
    )
