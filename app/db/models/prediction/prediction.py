from sqlalchemy import Float, String, DateTime, ForeignKey, func, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    p_home: Mapped[float] = mapped_column(Float, nullable=False)
    p_draw: Mapped[float] = mapped_column(Float, nullable=False)
    p_away: Mapped[float] = mapped_column(Float, nullable=False)

    p_over_1_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_under_1_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_over_2_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_under_2_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_over_3_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_under_3_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_btts_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_btts_no: Mapped[float | None] = mapped_column(Float, nullable=True)

    xg_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_away: Mapped[float | None] = mapped_column(Float, nullable=True)

    top_scorelines: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    data_quality: Mapped[str | None] = mapped_column(String(40), nullable=True)  
    features_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    match = relationship("Match", lazy="select")
    model = relationship("Model", lazy="select")

    __table_args__ = (
        Index("ix_predictions_match_model_time", "match_id", "model_id", "created_at"),
    )