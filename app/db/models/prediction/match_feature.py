from sqlalchemy import Float, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MatchFeature(Base):
    __tablename__ = "match_features"

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False)

    computed_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lambda_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    lambda_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    rating_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_diff: Mapped[float | None] = mapped_column(Float, nullable=True)

    home_goals_for_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_goals_against_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_goals_for_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_goals_against_avg: Mapped[float | None] = mapped_column(Float, nullable=True)

    features_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    match = relationship("Match", lazy="joined")
    model = relationship("Model", lazy="joined")

    __table_args__ = (
        UniqueConstraint("match_id", "model_id", name="uq_match_features_match_model"),
    )