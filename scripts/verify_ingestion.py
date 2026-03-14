"""
Verify ingestion coverage: count matches by league and season.

Usage:
    python -m scripts.verify_ingestion
"""
import sys

sys.path.insert(0, ".")

from sqlalchemy import func, select, extract
from app.db.session import SessionLocal
from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.db.models.football.league import League
from app.db.models.football.season import Season


def main() -> None:
    db = SessionLocal()
    try:
        # ── 1. Matches per league (finished vs scheduled) ──
        print("=" * 80)
        print("  MATCHES PER LEAGUE")
        print("=" * 80)

        stmt = (
            select(
                League.name,
                Match.status,
                func.count(Match.id).label("cnt"),
            )
            .join(League, Match.league_id == League.id)
            .group_by(League.name, Match.status)
            .order_by(League.name, Match.status)
        )
        rows = db.execute(stmt).all()
        current_league = None
        for league_name, status, cnt in rows:
            if league_name != current_league:
                print(f"\n  {league_name}:")
                current_league = league_name
            print(f"    {status:12s} → {cnt:4d}")

        # ── 2. Matches per league + season year ──
        print("\n" + "=" * 80)
        print("  MATCHES PER LEAGUE + SEASON YEAR")
        print("=" * 80)

        stmt2 = (
            select(
                League.name,
                Season.year,
                func.count(Match.id).label("cnt"),
            )
            .join(League, Match.league_id == League.id)
            .outerjoin(Season, Match.season_id == Season.id)
            .where(Match.status == "FINISHED")
            .group_by(League.name, Season.year)
            .order_by(League.name, Season.year)
        )
        rows2 = db.execute(stmt2).all()
        for league_name, year, cnt in rows2:
            year_str = str(year) if year else "NULL"
            print(f"  {league_name:30s} | season {year_str:6s} | {cnt:4d} finished")

        # ── 3. xG coverage (matches with stats) ──
        print("\n" + "=" * 80)
        print("  xG COVERAGE (matches with match_stats)")
        print("=" * 80)

        stmt3 = (
            select(
                League.name,
                func.count(func.distinct(Match.id)).label("total_finished"),
                func.count(func.distinct(MatchStats.match_id)).label("with_stats"),
            )
            .join(League, Match.league_id == League.id)
            .outerjoin(MatchStats, Match.id == MatchStats.match_id)
            .where(Match.status == "FINISHED")
            .group_by(League.name)
            .order_by(League.name)
        )
        rows3 = db.execute(stmt3).all()
        for league_name, total, with_stats in rows3:
            pct = round(with_stats / max(total, 1) * 100, 1)
            print(f"  {league_name:30s} | {with_stats:4d}/{total:4d} ({pct}%)")

        # ── 4. Date range per league ──
        print("\n" + "=" * 80)
        print("  DATE RANGE PER LEAGUE (finished matches)")
        print("=" * 80)

        stmt4 = (
            select(
                League.name,
                func.min(Match.utc_date).label("earliest"),
                func.max(Match.utc_date).label("latest"),
                func.count(Match.id).label("cnt"),
            )
            .join(League, Match.league_id == League.id)
            .where(Match.status == "FINISHED")
            .group_by(League.name)
            .order_by(League.name)
        )
        rows4 = db.execute(stmt4).all()
        for league_name, earliest, latest, cnt in rows4:
            e = earliest.strftime("%Y-%m-%d") if earliest else "?"
            l = latest.strftime("%Y-%m-%d") if latest else "?"
            print(f"  {league_name:30s} | {e} → {l} | {cnt:4d} matches")

        # ── 5. GRAND TOTAL ──
        total = db.scalar(select(func.count(Match.id)))
        finished = db.scalar(
            select(func.count(Match.id)).where(Match.status == "FINISHED")
        )
        print(f"\n  GRAND TOTAL: {total} matches ({finished} finished)")
        print("=" * 80)

    finally:
        db.close()


if __name__ == "__main__":
    main()
