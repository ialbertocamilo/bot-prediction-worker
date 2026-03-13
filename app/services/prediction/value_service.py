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
    """Convert decimal odds to normalised implied probabilities.

    Removes the bookmaker margin so P(home) + P(draw) + P(away) = 1.
    Returns dict with keys: p_home, p_draw, p_away, margin.
    """
    raw_home = 1.0 / home_odds if home_odds > 0 else 0.0
    raw_draw = 1.0 / draw_odds if draw_odds > 0 else 0.0
    raw_away = 1.0 / away_odds if away_odds > 0 else 0.0

    total = raw_home + raw_draw + raw_away
    if total <= 0:
        return {"p_home": 0.0, "p_draw": 0.0, "p_away": 0.0, "margin": 0.0}

    return {
        "p_home": round(raw_home / total, 6),
        "p_draw": round(raw_draw / total, 6),
        "p_away": round(raw_away / total, 6),
        "margin": round(total - 1.0, 6),
    }


def compute_edge(
    model_probs: dict[str, float],
    market_probs: dict[str, float],
) -> dict[str, float]:
    """Compute edge = P_model − P_market for each outcome.

    Positive edge → model thinks outcome is more likely than market.
    """
    return {
        "edge_home": round(model_probs.get("p_home", 0.0) - market_probs.get("p_home", 0.0), 6),
        "edge_draw": round(model_probs.get("p_draw", 0.0) - market_probs.get("p_draw", 0.0), 6),
        "edge_away": round(model_probs.get("p_away", 0.0) - market_probs.get("p_away", 0.0), 6),
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

        Returns up to *limit* entries sorted by max absolute edge descending.
        Only considers future SCHEDULED matches.
        """
        from datetime import datetime, timezone

        model_rec = self.model_repo.get_by_name(model_name)
        if model_rec is None:
            return []

        # Future matches with both odds and predictions
        stmt = (
            select(Match.id)
            .where(Match.status.in_(("SCHEDULED", "NS")))
            .where(Match.utc_date >= datetime.now(timezone.utc))
        )
        match_ids = [row[0] for row in self.db.execute(stmt)]

        results: list[dict] = []
        for mid in match_ids:
            val = self.match_value(mid, model_name)
            if val is None:
                continue

            edge = val["edge"]
            max_edge = max(edge["edge_home"], edge["edge_draw"], edge["edge_away"])
            if max_edge < min_edge:
                continue

            # Determine which outcome has the best edge
            best_outcome = max(
                [("home", edge["edge_home"]),
                 ("draw", edge["edge_draw"]),
                 ("away", edge["edge_away"])],
                key=lambda x: x[1],
            )
            val["best_value"] = {
                "outcome": best_outcome[0],
                "edge": best_outcome[1],
            }
            results.append(val)

        results.sort(key=lambda x: x["best_value"]["edge"], reverse=True)
        return results[:limit]
