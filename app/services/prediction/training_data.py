"""
Shared training data preparation for Dixon-Coles services.

Extracts the duplicated match data building, time-decay weighting,
xG prior accumulation, and xG map loading logic into reusable functions.

Used by: PredictionService, BacktestingService, RollingRetrainService.
"""
from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.services.prediction.dixon_coles import MatchData


def build_training_data(
    matches: list[Match],
    ref_ts: datetime,
    time_decay: float,
    xg_map: dict[int, dict[int, float]],
    min_xg_matches: int,
) -> tuple[list[MatchData], dict[int, tuple[float, float]]]:
    """Build MatchData list with time-decay weights and xG priors from raw matches.

    Args:
        matches: Historical matches (must have home_goals/away_goals).
        ref_ts: Reference timestamp for time-decay calculation.
        time_decay: Exponential decay rate (γ).
        xg_map: Pre-loaded xG data {match_id: {team_id: xg}}.
        min_xg_matches: Minimum xG-tracked matches per team for prior inclusion.

    Returns:
        Tuple of (match_data, xg_priors) where:
        - match_data: List of MatchData with time-decay weights
        - xg_priors: Dict {team_id: (avg_xg_for, avg_xg_against)}
    """
    match_data: list[MatchData] = []
    xg_for_lists: dict[int, list[float]] = {}
    xg_against_lists: dict[int, list[float]] = {}

    for m in matches:
        if m.home_goals is None or m.away_goals is None:
            continue
        days_ago = 0.0
        if m.utc_date and ref_ts:
            delta = (ref_ts - m.utc_date).total_seconds() / 86400.0
            days_ago = max(delta, 0.0)
        w = math.exp(-time_decay * days_ago)

        match_data.append(MatchData(
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
            home_goals=m.home_goals,
            away_goals=m.away_goals,
            weight=w,
        ))

        pair = xg_map.get(m.id, {})
        h_xg = pair.get(m.home_team_id)
        a_xg = pair.get(m.away_team_id)
        if h_xg is not None and a_xg is not None:
            xg_for_lists.setdefault(m.home_team_id, []).append(h_xg)
            xg_against_lists.setdefault(m.home_team_id, []).append(a_xg)
            xg_for_lists.setdefault(m.away_team_id, []).append(a_xg)
            xg_against_lists.setdefault(m.away_team_id, []).append(h_xg)

    xg_priors: dict[int, tuple[float, float]] = {}
    for tid in set(xg_for_lists) & set(xg_against_lists):
        n_matches = len(xg_for_lists[tid])
        if n_matches < min_xg_matches:
            continue
        avg_for = sum(xg_for_lists[tid]) / n_matches
        avg_against = sum(xg_against_lists[tid]) / n_matches
        xg_priors[tid] = (avg_for, avg_against)

    return match_data, xg_priors


def load_xg_map(
    db: Session,
    match_ids: list[int],
    batch_size: int = 500,
) -> dict[int, dict[int, float]]:
    """Load xG values from match_stats for given match IDs.

    Returns ``{match_id: {team_id: xg}}``.
    """
    if not match_ids:
        return {}
    result: dict[int, dict[int, float]] = {}
    for start in range(0, len(match_ids), batch_size):
        batch = match_ids[start: start + batch_size]
        stmt = (
            select(MatchStats.match_id, MatchStats.team_id, MatchStats.xg)
            .where(MatchStats.match_id.in_(batch))
            .where(MatchStats.xg.isnot(None))
        )
        for row in db.execute(stmt):
            result.setdefault(row.match_id, {})[row.team_id] = row.xg
    return result
