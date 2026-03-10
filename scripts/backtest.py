"""
Run walk-forward backtesting on the Dixon-Coles model.

Usage:
    python scripts/backtest.py                     # all leagues
    python scripts/backtest.py --league-id 1       # specific league
"""
from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services.prediction.backtesting_service import BacktestingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Dixon-Coles model")
    parser.add_argument("--league-id", type=int, default=None, help="Filter by league ID")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        svc = BacktestingService(db, league_id=args.league_id)
        report = svc.run()
        print(report.summary())
    finally:
        db.close()


if __name__ == "__main__":
    main()
