"""
Audit xG coverage: list teams in a league that are missing xG data.

Usage:
    python -m scripts.audit_xg_coverage              # defaults to Spanish LaLiga
    python -m scripts.audit_xg_coverage --league "English Premier League"
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select, and_

from app.db.session import SessionLocal
from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.db.models.football.team import Team


def audit(league_pattern: str = "LaLiga") -> None:
    db = SessionLocal()
    try:
        # Resolve league IDs matching the pattern
        leagues = list(db.scalars(
            select(League).where(League.name.ilike(f"%{league_pattern}%"))
        ).all())

        if not leagues:
            print(f"No leagues matching '{league_pattern}'")
            return

        league_ids = [lg.id for lg in leagues]
        print(f"Leagues matched: {[(lg.id, lg.name) for lg in leagues]}")
        print()

        # All teams that played FINISHED matches in these leagues
        home_q = (
            select(Match.home_team_id.label("team_id"))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
        )
        away_q = (
            select(Match.away_team_id.label("team_id"))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
        )
        all_team_ids = set(db.scalars(home_q).all()) | set(db.scalars(away_q).all())

        if not all_team_ids:
            print("No finished matches found.")
            return

        # Matches in these leagues
        match_ids_q = (
            select(Match.id)
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
        )
        match_ids = set(db.scalars(match_ids_q).all())

        # Count xG entries per team (from match_stats where xg IS NOT NULL)
        xg_counts_q = (
            select(
                MatchStats.team_id,
                func.count(MatchStats.id).label("xg_matches"),
            )
            .where(MatchStats.match_id.in_(match_ids))
            .where(MatchStats.xg.isnot(None))
            .group_by(MatchStats.team_id)
        )
        xg_map: dict[int, int] = {}
        for row in db.execute(xg_counts_q):
            xg_map[row[0]] = row[1]

        # Count total matches per team
        total_home = (
            select(
                Match.home_team_id.label("team_id"),
                func.count(Match.id).label("cnt"),
            )
            .where(Match.id.in_(match_ids))
            .group_by(Match.home_team_id)
        )
        total_away = (
            select(
                Match.away_team_id.label("team_id"),
                func.count(Match.id).label("cnt"),
            )
            .where(Match.id.in_(match_ids))
            .group_by(Match.away_team_id)
        )
        total_map: dict[int, int] = {}
        for row in db.execute(total_home):
            total_map[row[0]] = total_map.get(row[0], 0) + row[1]
        for row in db.execute(total_away):
            total_map[row[0]] = total_map.get(row[0], 0) + row[1]

        # Load team names
        teams = {t.id: t.name for t in db.scalars(
            select(Team).where(Team.id.in_(all_team_ids))
        ).all()}

        # Report
        print(f"{'Team':<35} {'Matches':>8} {'xG entries':>11} {'Coverage':>9}")
        print("-" * 68)

        missing = []
        for tid in sorted(all_team_ids):
            name = teams.get(tid, f"ID={tid}")
            total = total_map.get(tid, 0)
            xg_n = xg_map.get(tid, 0)
            pct = (xg_n / total * 100) if total else 0
            flag = " *** MISSING" if xg_n == 0 else (" * LOW" if pct < 50 else "")
            print(f"{name:<35} {total:>8} {xg_n:>11} {pct:>8.0f}%{flag}")
            if xg_n == 0:
                missing.append(name)

        print()
        print(f"Total teams: {len(all_team_ids)}")
        print(f"Teams with xG: {len(xg_map)}")
        print(f"Teams WITHOUT xG: {len(all_team_ids) - len(xg_map)}")
        if missing:
            print(f"\nMissing teams: {', '.join(missing)}")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit xG coverage for a league")
    parser.add_argument("--league", default="LaLiga", help="League name pattern (case-insensitive)")
    args = parser.parse_args()
    audit(args.league)
