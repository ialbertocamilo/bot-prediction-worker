from sqlalchemy import Float, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PredictionEval(Base):
    __tablename__ = "prediction_eval"

    id: Mapped[int] = mapped_column(primary_key=True)

    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)

    evaluated_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actual_outcome: Mapped[str] = mapped_column(String(10), nullable=False) 

    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)

    prediction = relationship("Prediction", lazy="joined")
    match = relationship("Match", lazy="joined")

    __table_args__ = (
        UniqueConstraint("prediction_id", "match_id", name="uq_prediction_eval_pred_match"),
    )