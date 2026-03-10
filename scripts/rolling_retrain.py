"""
Rolling retraining — update team ratings incrementally after each match.

Usage:
    python scripts/rolling_retrain.py --league-id 2
    python scripts/rolling_retrain.py --league-id 2 --from-date 2026-01-01
    python scripts/rolling_retrain.py --league-id 2 --dry-run

Leagues:
    --league-id 2  "Peruvian Liga 1"  → 54 partidos, xG ~43%  (recomendado)
    --league-id 1  "Primera División"  → 306 partidos, xG <1%
    IMPORTANTE: No mezclar ligas — equipos duplicados con IDs distintos.
    Siempre filtrar con --league-id para evitar datos cruzados.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services.prediction.rolling_retrain_service import RollingRetrainService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Silence per-fit xG prior logs
logging.getLogger("app.services.prediction.dixon_coles").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rolling retrain Dixon-Coles ratings")
    parser.add_argument("--league-id", type=int, default=None, help="ID de liga (default: todas)")
    parser.add_argument("--from-date", type=str, default=None, help="Fecha inicio YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Calcular sin persistir en DB")
    args = parser.parse_args()

    from_dt = None
    if args.from_date:
        from_dt = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    db = SessionLocal()
    try:
        svc = RollingRetrainService(
            db=db,
            league_id=args.league_id,
            from_date=from_dt,
            dry_run=args.dry_run,
        )
        report = svc.run()
        print(report.summary())
    finally:
        db.close()


if __name__ == "__main__":
    main()
