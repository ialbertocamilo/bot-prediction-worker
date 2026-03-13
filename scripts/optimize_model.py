"""
Grid-search hyperparameter optimization for Dixon-Coles model.

Usage:
    python scripts/optimize_model.py --mode coarse --league-id 2   # fast 27 combos
    python scripts/optimize_model.py --mode fine --league-id 2     # coarse → fine (recomendado)
    python scripts/optimize_model.py --mode full --league-id 1     # all 294 combos

Leagues:
    --league-id 2  "Peruvian Liga 1"  → 54 partidos, xG ~43%  (recomendado)
    --league-id 1  "Primera División"  → 306 partidos, xG <1%
    IMPORTANTE: No mezclar ligas — equipos duplicados con IDs distintos.
    Siempre filtrar con --league-id.
"""
from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services.prediction.hyperparameter_optimization_service import (
    HyperparameterOptimizationService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Silence per-fit xG prior logs to reduce noise during grid search
logging.getLogger("app.services.prediction.dixon_coles").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimización de hiperparámetros Dixon-Coles")
    parser.add_argument("--league-id", type=int, default=None, help="ID de liga (default: todas)")
    parser.add_argument(
        "--mode",
        choices=["coarse", "fine", "full", "random"],
        default="fine",
        help="coarse: 27 combos | fine: coarse→refine (default) | full: 294 combos | random: n_iter samples",
    )
    parser.add_argument(
        "--n-iter",
        type=int,
        default=30,
        help="Number of random combos to sample (only for --mode random, default: 30)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        svc = HyperparameterOptimizationService(db=db, league_id=args.league_id)
        if args.mode == "coarse":
            report = svc.run_coarse()
        elif args.mode == "fine":
            report = svc.run_fine()
        elif args.mode == "random":
            report = svc.run_random(n_iter=args.n_iter)
        else:
            report = svc.run()
        print(report.summary())
    finally:
        db.close()


if __name__ == "__main__":
    main()
