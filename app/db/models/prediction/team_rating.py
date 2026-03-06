from sqlalchemy import Float, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TeamRating(Base):
    __tablename__ = "team_ratings"

    id: Mapped[int] = mapped_column(primary_key=True)

    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.id", ondelete="SET NULL"), nullable=True)

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)

    rating: Mapped[float] = mapped_column(Float, nullable=False)

    as_of_date: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of_match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    model = relationship("Model", lazy="joined")
    team = relationship("Team", lazy="joined")
    season = relationship("Season", lazy="joined")
    as_of_match = relationship("Match", lazy="joined")

    __table_args__ = (
        Index("ix_team_ratings_model_team_date", "model_id", "team_id", "as_of_date"),
    )