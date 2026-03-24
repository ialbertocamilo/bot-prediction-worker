"""
Discover leagues — Utility to inspect which leagues exist in the database
and how many matches each has.  Helps populate db_league_ids in LEAGUE_GROUPS.

Usage:
    python -m scripts.discover_leagues
    python -m scripts.discover_leagues --min-matches 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import case, func, select
from app.db.session import SessionLocal
from app.db.models.football.league import League
from app.db.models.football.match import Match


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover leagues in the DB")
    parser.add_argument(
        "--min-matches", type=int, default=0,
        help="Only show leagues with at least this many total matches (default: 0)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Single query: league info + match counts grouped
        stmt = (
            select(
                League.id,
                League.name,
                League.country,
                func.count(Match.id).label("total"),
                func.sum(
                    case(
                        (Match.status.in_(("FINISHED", "FT")), 1),
                        else_=0,
                    )
                ).label("finished"),
                func.sum(
                    case(
                        (Match.status.in_(("SCHEDULED", "NS")), 1),
                        else_=0,
                    )
                ).label("scheduled"),
            )
            .outerjoin(Match, Match.league_id == League.id)
            .group_by(League.id, League.name, League.country)
            .order_by(League.country.asc(), func.count(Match.id).desc())
        )
        rows = list(db.execute(stmt))

        # Filter by minimum matches
        rows = [r for r in rows if r.total >= args.min_matches]

        if not rows:
            print("No leagues found in the database.")
            return

        # Print header
        print()
        print(f"{'ID':>5}  {'League Name':<40} {'Country':<16} {'Total':>6} {'Fin':>6} {'Sched':>6}")
        print("-" * 95)

        current_country = None
        for r in rows:
            country = r.country or "(unknown)"
            if country != current_country:
                if current_country is not None:
                    print()  # blank line between countries
                current_country = country
            print(
                f"{r.id:>5}  {r.name:<40} {country:<16} {r.total:>6} {r.finished:>6} {r.scheduled:>6}"
            )

        # Summary
        total_leagues = len(rows)
        total_matches = sum(r.total for r in rows)
        total_finished = sum(r.finished for r in rows)
        print()
        print(f"Total: {total_leagues} leagues, {total_matches} matches ({total_finished} finished)")

        # Show IDs grouped by country for easy copy-paste
        print("\n\n=== Quick copy-paste reference (Country → IDs) ===\n")
        by_country: dict[str, list[tuple[int, str, int]]] = {}
        for r in rows:
            c = r.country or "(unknown)"
            by_country.setdefault(c, []).append((r.id, r.name, r.total))
        for country in sorted(by_country):
            entries = by_country[country]
            ids_str = ", ".join(str(e[0]) for e in entries)
            print(f"  {country:<20} → [{ids_str}]")
            for lid, name, total in entries:
                print(f"      {lid:>4}: {name} ({total} matches)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
