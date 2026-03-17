"""
Value detection service — converts market odds to probabilities,
computes edge (model vs market), and identifies value bets.

Provider-agnostic: works purely on data already in the DB.
"""
from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.prediction.market_odds import MarketOdds
from app.db.models.prediction.prediction import Prediction
from app.repositories.prediction.market_odds_repository import MarketOddsRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository

logger = logging.getLogger(__name__)


def odds_to_probs(home_odds: float, draw_odds: float, away_odds: float) -> dict[str, float]:
    """Convert decimal odds to fair implied probabilities using the power method.

    The power method (Shin-style) finds exponent k such that
    (1/home)^k + (1/draw)^k + (1/away)^k = 1,
    correcting for the favourite-longshot bias inherent in naive normalisation.
    Falls back to proportional normalisation if the solver fails.

    Returns dict with keys: p_home, p_draw, p_away, margin.
    """
    raw_home = 1.0 / home_odds if home_odds > 0 else 0.0
    raw_draw = 1.0 / draw_odds if draw_odds > 0 else 0.0
    raw_away = 1.0 / away_odds if away_odds > 0 else 0.0

    total = raw_home + raw_draw + raw_away
    if total <= 0:
        return {"p_home": 0.0, "p_draw": 0.0, "p_away": 0.0, "margin": 0.0}

    margin = total - 1.0
    raws = [raw_home, raw_draw, raw_away]

    # Power method: find k via bisection so sum(raw_i^k) = 1
    # When margin > 0 the solution k > 1; when margin ≈ 0, k ≈ 1.
    if abs(margin) < 1e-6:
        # Already fair odds — no correction needed
        fair = [round(r, 6) for r in raws]
    else:
        lo, hi = 1.0, 2.0
        # Expand upper bound if needed
        for _ in range(20):
            if sum(r ** hi for r in raws) < 1.0:
                break
            hi *= 2.0
        # Bisection: 50 iterations gives precision ~1e-15
        for _ in range(50):
            mid = (lo + hi) / 2.0
            val = sum(r ** mid for r in raws)
            if val > 1.0:
                lo = mid
            else:
                hi = mid
        k = (lo + hi) / 2.0
        fair = [round(r ** k, 6) for r in raws]

    return {
        "p_home": fair[0],
        "p_draw": fair[1],
        "p_away": fair[2],
        "margin": round(margin, 6),
    }


def compute_edge(
    model_probs: dict[str, float],
    market_probs: dict[str, float],
) -> dict[str, float]:
    """Compute multiplicative edge = P_model / P_market − 1 for each outcome.

    Equivalent to EV = P_model × odds − 1 when market probs are fair.
    Positive edge → model thinks outcome is more likely than market implies.
    A 10% edge at odds 2.0 and 10% edge at odds 20.0 are now correctly
    comparable (both represent 10% expected profit on stake).
    """
    edges: dict[str, float] = {}
    for key, m_key in [("edge_home", "p_home"), ("edge_draw", "p_draw"), ("edge_away", "p_away")]:
        p_model = model_probs.get(m_key, 0.0)
        p_market = market_probs.get(m_key, 0.0)
        if p_market > 1e-9:
            edges[key] = round(p_model / p_market - 1.0, 6)
        else:
            edges[key] = 0.0
    return edges


def compute_kelly_stake(
    p_model: float,
    odds: float,
    kelly_fraction: float | None = None,
    max_stake_pct: float | None = None,
) -> dict[str, float]:
    """Compute recommended stake % using fractional Kelly criterion.

    Formula (traditional Kelly):
        f* = (p * (O - 1) - (1 - p)) / (O - 1)
    where p = model probability and O = decimal odds.

    Returns dict with:
        kelly_raw       – full (uncapped) Kelly fraction
        recommended_stake_percent – fraction * kelly * capped to max_stake_pct
        edge            – additive edge = p * O - 1  (>0 → positive EV)

    If edge <= 0 the recommended stake is always 0.
    """
    from config import KELLY_FRACTION, MAX_STAKE_PERCENT

    if kelly_fraction is None:
        kelly_fraction = KELLY_FRACTION
    if max_stake_pct is None:
        max_stake_pct = MAX_STAKE_PERCENT

    b = odds - 1.0
    edge = p_model * odds - 1.0  # additive edge

    if edge <= 0 or b <= 0:
        return {
            "kelly_raw": 0.0,
            "recommended_stake_percent": 0.0,
            "edge": round(edge, 6),
        }

    kelly_f = (b * p_model - (1.0 - p_model)) / b  # = (p*O - 1) / (O - 1)
    kelly_f = max(kelly_f, 0.0)

    stake_pct = kelly_f * kelly_fraction
    stake_pct = min(stake_pct, max_stake_pct)

    return {
        "kelly_raw": round(kelly_f, 6),
        "recommended_stake_percent": round(stake_pct, 6),
        "edge": round(edge, 6),
    }


class ValueService:
    """Compares model predictions against market odds to find value."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.odds_repo = MarketOddsRepository(db)
        self.pred_repo = PredictionRepository(db)
        self.model_repo = ModelRepository(db)

    def match_value(self, match_id: int, model_name: str = "dixon_coles_v1") -> dict | None:
        """Compute model vs market comparison for a single match.

        Returns None if either prediction or odds are missing.
        """
        model_rec = self.model_repo.get_by_name(model_name)
        if model_rec is None:
            return None

        pred = self.pred_repo.latest_for_match_and_model(match_id, model_rec.id)
        if pred is None:
            return None

        consensus = self.odds_repo.consensus_for_match(match_id)
        if consensus is None:
            return None

        market = odds_to_probs(
            consensus["home_odds"],
            consensus["draw_odds"],
            consensus["away_odds"],
        )
        model = {"p_home": pred.p_home, "p_draw": pred.p_draw, "p_away": pred.p_away}
        edge = compute_edge(model, market)

        return {
            "match_id": match_id,
            "model_probabilities": model,
            "market_odds": {
                "home": consensus["home_odds"],
                "draw": consensus["draw_odds"],
                "away": consensus["away_odds"],
                "bookmakers": consensus["bookmakers"],
            },
            "market_probabilities": market,
            "edge": edge,
            "margin": market["margin"],
        }

    def top_value_bets(
        self,
        min_edge: float = 0.03,
        limit: int = 20,
        model_name: str = "dixon_coles_v1",
    ) -> list[dict]:
        """Find matches with the highest positive edge.

        Returns up to *limit* entries sorted by max edge descending.
        Only considers future SCHEDULED matches.
        Uses batch queries instead of per-match lookups (N+1 fix).
        """
        from datetime import datetime, timezone

        model_rec = self.model_repo.get_by_name(model_name)
        if model_rec is None:
            return []

        # Batch: future matches with predictions (single query)
        stmt = (
            select(Match.id, Prediction.p_home, Prediction.p_draw, Prediction.p_away)
            .join(Prediction, Prediction.match_id == Match.id)
            .where(Match.status.in_(("SCHEDULED", "NS")))
            .where(Match.utc_date >= datetime.now(timezone.utc))
            .where(Prediction.model_id == model_rec.id)
        )
        pred_rows = list(self.db.execute(stmt))
        if not pred_rows:
            return []

        match_ids = [r[0] for r in pred_rows]
        pred_map = {r[0]: (float(r[1]), float(r[2]), float(r[3])) for r in pred_rows}

        # Batch: consensus odds for all matches (single query)
        odds_stmt = (
            select(MarketOdds.match_id, MarketOdds.home_odds, MarketOdds.draw_odds, MarketOdds.away_odds)
            .where(MarketOdds.match_id.in_(match_ids))
        )
        odds_agg: dict[int, dict[str, list[float]]] = {}
        for r in self.db.execute(odds_stmt):
            mid = r.match_id
            if mid not in odds_agg:
                odds_agg[mid] = {"home": [], "draw": [], "away": []}
            odds_agg[mid]["home"].append(r.home_odds)
            odds_agg[mid]["draw"].append(r.draw_odds)
            odds_agg[mid]["away"].append(r.away_odds)

        results: list[dict] = []
        for mid in match_ids:
            if mid not in pred_map or mid not in odds_agg:
                continue

            p_h, p_d, p_a = pred_map[mid]
            od = odds_agg[mid]
            n = len(od["home"])
            h_odds = sum(od["home"]) / n
            d_odds = sum(od["draw"]) / n
            a_odds = sum(od["away"]) / n

            market = odds_to_probs(h_odds, d_odds, a_odds)
            model = {"p_home": p_h, "p_draw": p_d, "p_away": p_a}
            edge = compute_edge(model, market)

            max_edge = max(edge["edge_home"], edge["edge_draw"], edge["edge_away"])
            if max_edge < min_edge:
                continue

            best_outcome = max(
                [("home", edge["edge_home"]),
                 ("draw", edge["edge_draw"]),
                 ("away", edge["edge_away"])],
                key=lambda x: x[1],
            )
            results.append({
                "match_id": mid,
                "model_probabilities": model,
                "market_odds": {
                    "home": round(h_odds, 3),
                    "draw": round(d_odds, 3),
                    "away": round(a_odds, 3),
                    "bookmakers": n,
                },
                "market_probabilities": market,
                "edge": edge,
                "margin": market["margin"],
                "best_value": {
                    "outcome": best_outcome[0],
                    "edge": best_outcome[1],
                },
            })

        results.sort(key=lambda x: x["best_value"]["edge"], reverse=True)
        return results[:limit]
