"""
Seed all configured leagues — ingest results + fixtures from ESPN.

Usage:
    python -m scripts.seed_leagues              # all leagues, default range
    python -m scripts.seed_leagues --days-back 90 --days-ahead 14
    python -m scripts.seed_leagues --league champions-league   # single league
"""
import argparse
import logging
import sys

sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.services.canonical_league_service import (
    LEAGUE_GROUPS,
    CanonicalLeagueService,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed leagues from ESPN")
    parser.add_argument(
        "--league", type=str, default=None,
        help="Key of a single league group to seed (e.g. 'premier-league')",
    )
    parser.add_argument("--days-back", type=int, default=180)
    parser.add_argument("--days-ahead", type=int, default=30)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        svc = CanonicalLeagueService(db)

        if args.league:
            valid_keys = [g.key for g in LEAGUE_GROUPS]
            if args.league not in valid_keys:
                logger.error(
                    "League key '%s' not found. Valid keys: %s",
                    args.league, ", ".join(valid_keys),
                )
                return
            n = svc.ingest_league(
                args.league,
                days_back=args.days_back,
                days_ahead=args.days_ahead,
            )
            logger.info("Done: %d matches ingested for '%s'", n, args.league)
        else:
            total = svc.seed_all_leagues(
                days_back=args.days_back,
                days_ahead=args.days_ahead,
            )
            logger.info("Done: %d total matches ingested", total)

        # Summary
        logger.info("=== RESUMEN ===")
        # Reload service to get fresh resolved IDs
        svc2 = CanonicalLeagueService(db)
        for info in svc2.list_leagues():
            logger.info(
                "  %d. %-25s %3d finished | %2d scheduled  (db_ids=%s)",
                info.index, info.display_name,
                info.finished_matches, info.scheduled_matches,
                info.db_league_ids,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
