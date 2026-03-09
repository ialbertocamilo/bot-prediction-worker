"""Quick test prediction."""
import traceback
from app.db.session import SessionLocal
from app.services.prediction.prediction_service import PredictionService

db = SessionLocal()
try:
    svc = PredictionService(db)
    r = svc.predict_match(1)
    print("OK:", r)
except Exception:
    traceback.print_exc()
finally:
    db.close()

