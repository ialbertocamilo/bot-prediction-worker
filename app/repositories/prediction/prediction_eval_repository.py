from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.prediction_eval import PredictionEval


class PredictionEvalRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_prediction_and_match(
        self,
        prediction_id: int,
        match_id: int,
    ) -> PredictionEval | None:
        stmt = (
            select(PredictionEval)
            .where(PredictionEval.prediction_id == prediction_id)
            .where(PredictionEval.match_id == match_id)
        )
        return self.db.scalar(stmt)

    def create(
        self,
        prediction_id: int,
        match_id: int,
        actual_outcome: str,
        brier_score: float | None = None,
        log_loss: float | None = None,
    ) -> PredictionEval:
        prediction_eval: PredictionEval = PredictionEval(
            prediction_id=prediction_id,
            match_id=match_id,
            actual_outcome=actual_outcome,
            brier_score=brier_score,
            log_loss=log_loss,
        )
        self.db.add(prediction_eval)
        self.db.flush()
        self.db.refresh(prediction_eval)
        return prediction_eval