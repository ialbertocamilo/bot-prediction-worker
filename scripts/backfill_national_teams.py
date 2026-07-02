"""
Backfill national teams in the DB based on matches from national-team competitions.

Usage:
    python -m scripts.backfill_national_teams --dry-run
    python -m scripts.backfill_national_teams --apply
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata

from sqlalchemy import distinct, or_, select

sys.path.insert(0, ".")

from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.models.football.team import Team
from app.db.session import SessionLocal
from app.services.canonical_league_service import LEAGUE_GROUPS

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

NATIONAL_COMPETITION_KEYS = {
    "world-cup",
    "wcq-conmebol",
    "wcq-uefa",
    "copa-america",
    "euro",
    "intl-friendly",
}

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bwinner\b", re.IGNORECASE),
    re.compile(r"\brunner[\s-]?up\b", re.IGNORECASE),
    re.compile(r"\bloser\b", re.IGNORECASE),
    re.compile(r"\bgroup\s+[a-z0-9]+\b", re.IGNORECASE),
    re.compile(r"\bquarterfinal\b", re.IGNORECASE),
    re.compile(r"\bsemifinal\b", re.IGNORECASE),
    re.compile(r"\bfinalist\b", re.IGNORECASE),
    re.compile(r"\bmatch\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bslot\b", re.IGNORECASE),
    re.compile(r"\btbd\b", re.IGNORECASE),
    re.compile(r"\bto be determined\b", re.IGNORECASE),
    re.compile(r"\bunknown\b", re.IGNORECASE),
)


def _national_league_names() -> list[str]:
    names: list[str] = []
    for group in LEAGUE_GROUPS:
        if group.key in NATIONAL_COMPETITION_KEYS:
            names.extend(group.league_names)
    return sorted(set(names))


def _is_placeholder_team(name: str) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return True
    return any(pattern.search(cleaned) for pattern in _PLACEHOLDER_PATTERNS)


def _slugify_national_key(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_name = ascii_name.lower()
    ascii_name = re.sub(r"[^a-z0-9]+", "-", ascii_name)
    return re.sub(r"-+", "-", ascii_name).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill national teams in DB")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes to the database. Default is dry-run.",
    )
    args = parser.parse_args()

    league_names = _national_league_names()
    db = SessionLocal()
    try:
        stmt = (
            select(Team)
            .join(
                Match,
                or_(Team.id == Match.home_team_id, Team.id == Match.away_team_id),
            )
            .join(League, League.id == Match.league_id)
            .where(League.name.in_(league_names))
            .distinct()
            .order_by(Team.name.asc())
        )
        teams = list(db.scalars(stmt).all())

        updated = 0
        cleared_domestic = 0
        assigned_keys = 0
        skipped_placeholders: list[str] = []
        changed_samples: list[str] = []

        logger.info("Found %d teams participating in national-team competitions", len(teams))

        for team in teams:
            if _is_placeholder_team(team.name):
                skipped_placeholders.append(team.name)
                continue

            before_type = team.team_type
            before_domestic = team.domestic_league_key
            before_national = team.national_team_key

            desired_key = _slugify_national_key(team.name)
            changed = False

            if team.team_type != "NATIONAL":
                team.team_type = "NATIONAL"
                changed = True
            if team.national_team_key != desired_key:
                team.national_team_key = desired_key
                changed = True
            if team.domestic_league_key is not None:
                team.domestic_league_key = None
                cleared_domestic += 1
                changed = True

            if changed:
                updated += 1
                if before_national != desired_key:
                    assigned_keys += 1
                if len(changed_samples) < 20:
                    changed_samples.append(
                        f"{team.id} | {team.name} | type: {before_type} -> {team.team_type} | "
                        f"domestic: {before_domestic} -> {team.domestic_league_key} | "
                        f"national: {before_national} -> {team.national_team_key}"
                    )

        logger.info("Mode: %s", "APPLY" if args.apply else "DRY-RUN")
        logger.info("Teams updated: %d", updated)
        logger.info("Domestic keys cleared: %d", cleared_domestic)
        logger.info("National keys assigned/updated: %d", assigned_keys)
        logger.info("Placeholders skipped: %d", len(skipped_placeholders))

        for line in changed_samples:
            logger.info("Changed: %s", line)

        if skipped_placeholders:
            for name in skipped_placeholders[:20]:
                logger.info("Skipped placeholder: %s", name)

        if args.apply:
            db.commit()
            logger.info("Backfill committed successfully.")
        else:
            db.rollback()
            logger.info("Dry-run complete. No changes were committed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
