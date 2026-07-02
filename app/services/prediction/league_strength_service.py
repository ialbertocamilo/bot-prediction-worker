from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.repositories.prediction.league_strength_repository import (
    LeagueStrengthRepository,
)

logger = logging.getLogger(__name__)

SMOOTHING_PRIOR_SAMPLE_SIZE = 20
SMOOTHING_PRIOR_COEFFICIENT = 1.0


@dataclass(slots=True)
class LeagueStrengthRecord:
    league_key: str
    coefficient: float
    sample_size: int


@dataclass(slots=True)
class LeagueStrengthRefreshReport:
    matches_used: int
    leagues_updated: int
    scale: float
    records: list[LeagueStrengthRecord]


class LeagueStrengthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = LeagueStrengthRepository(db)

    def recompute(self) -> LeagueStrengthRefreshReport:
        matches = self._load_cross_league_matches()
        records, scale = self.compute_from_matches(matches)
        for rec in records:
            self.repo.upsert(
                league_key=rec.league_key,
                coefficient=rec.coefficient,
                sample_size=rec.sample_size,
            )
        self.db.flush()
        return LeagueStrengthRefreshReport(
            matches_used=len(matches),
            leagues_updated=len(records),
            scale=scale,
            records=records,
        )

    @classmethod
    def compute_from_matches(
        cls,
        matches: list[Match],
    ) -> tuple[list[LeagueStrengthRecord], float]:
        goal_diffs: list[float] = []
        rows: list[tuple[str, str, float]] = []
        for match in matches:
            home_key = getattr(match.home_team, "domestic_league_key", None)
            away_key = getattr(match.away_team, "domestic_league_key", None)
            if not home_key or not away_key or home_key == away_key:
                continue
            if getattr(match.home_team, "team_type", "CLUB") != "CLUB":
                continue
            if getattr(match.away_team, "team_type", "CLUB") != "CLUB":
                continue
            if match.home_goals is None or match.away_goals is None:
                continue
            gd = float(match.home_goals - match.away_goals)
            goal_diffs.append(gd)
            rows.append((home_key, away_key, gd))

        if not rows:
            return [], 1.0

        scale = pstdev(goal_diffs) if len(goal_diffs) > 1 else abs(goal_diffs[0])
        if scale <= 0:
            scale = 1.0

        contributions: dict[str, list[float]] = defaultdict(list)
        for home_key, away_key, gd in rows:
            normalized = gd / scale
            home_value = math.exp(normalized)
            away_value = math.exp(-normalized)
            contributions[home_key].append(home_value)
            contributions[away_key].append(away_value)

        records: list[LeagueStrengthRecord] = []
        for league_key in sorted(contributions):
            samples = contributions[league_key]
            sample_size = len(samples)
            raw_coefficient = sum(samples) / sample_size
            smoothed = (
                raw_coefficient * sample_size
                + SMOOTHING_PRIOR_COEFFICIENT * SMOOTHING_PRIOR_SAMPLE_SIZE
            ) / (sample_size + SMOOTHING_PRIOR_SAMPLE_SIZE)
            records.append(
                LeagueStrengthRecord(
                    league_key=league_key,
                    coefficient=smoothed,
                    sample_size=sample_size,
                )
            )

        return records, scale

    def _load_cross_league_matches(self) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.asc())
        )
        return list(self.db.scalars(stmt).all())
