from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.prediction.prediction_service import PredictionService

router = APIRouter()


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/{match_id}")
def predict(match_id: int, db: Session = Depends(_get_db)):
    service = PredictionService(db)
    result = service.predict_match(match_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No se pudo generar predicción. Verifica el ID y que haya datos suficientes.",
        )
    return result