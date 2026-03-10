from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.prediction import Prediction


class PredictionRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, prediction_id: int) -> Prediction | None:
        return self.db.get(Prediction, prediction_id)

    def create(
        self,
        match_id: int,
        model_id: int,
        p_home: float,
        p_draw: float,
        p_away: float,
        p_over_1_5: float | None = None,
        p_under_1_5: float | None = None,
        p_over_2_5: float | None = None,
        p_under_2_5: float | None = None,
        p_over_3_5: float | None = None,
        p_under_3_5: float | None = None,
        p_btts_yes: float | None = None,
        p_btts_no: float | None = None,
        xg_home: float | None = None,
        xg_away: float | None = None,
        top_scorelines: dict | None = None,
        data_quality: str | None = None,
        features_hash: str | None = None,
    ) -> Prediction:
        prediction: Prediction = Prediction(
            match_id=match_id,
            model_id=model_id,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            p_over_1_5=p_over_1_5,
            p_under_1_5=p_under_1_5,
            p_over_2_5=p_over_2_5,
            p_under_2_5=p_under_2_5,
            p_over_3_5=p_over_3_5,
            p_under_3_5=p_under_3_5,
            p_btts_yes=p_btts_yes,
            p_btts_no=p_btts_no,
            xg_home=xg_home,
            xg_away=xg_away,
            top_scorelines=top_scorelines,
            data_quality=data_quality,
            features_hash=features_hash,
        )
        self.db.add(prediction)
        self.db.flush()
        self.db.refresh(prediction)
        return prediction

    def latest_for_match_and_model(
        self,
        match_id: int,
        model_id: int,
    ) -> Prediction | None:
        stmt = (
            select(Prediction)
            .where(Prediction.match_id == match_id)
            .where(Prediction.model_id == model_id)
            .order_by(Prediction.created_at.desc(), Prediction.id.desc())
        )
        return self.db.scalars(stmt).first()

    def list_for_match(
        self,
        match_id: int,
    ) -> list[Prediction]:
        stmt = (
            select(Prediction)
            .where(Prediction.match_id == match_id)
            .order_by(Prediction.created_at.desc(), Prediction.id.desc())
        )
        return list(self.db.scalars(stmt).all())