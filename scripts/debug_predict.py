"""Debug script to capture the exact error from predict_match."""
import sys
import traceback
import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s",
                    stream=sys.stdout)

sys.stdout.write("=== SCRIPT START ===\n")
sys.stdout.flush()

from app.db.session import SessionLocal
from app.services.prediction.prediction_service import PredictionService

sys.stdout.write("=== IMPORTS OK ===\n")
sys.stdout.flush()

db = SessionLocal()
sys.stdout.write("=== DB SESSION OK ===\n")
sys.stdout.flush()

try:
    svc = PredictionService(db)
    sys.stdout.write("=== SERVICE OK ===\n")
    sys.stdout.flush()
    sys.stdout.write("Calling predict_match(1)...\n")
    sys.stdout.flush()
    result = svc.predict_match(1)
    sys.stdout.write(f"SUCCESS: {result}\n")
    sys.stdout.flush()
except Exception as e:
    sys.stdout.write(f"ERROR: {e}\n")
    sys.stdout.flush()
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
finally:
    db.close()
    sys.stdout.write("=== DONE ===\n")
    sys.stdout.flush()
