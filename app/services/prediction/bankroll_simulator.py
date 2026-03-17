"""
Bankroll simulator — runs hypothetical betting simulations using
historical model predictions vs market odds.

Strategies:
  - FlatStake: fixed stake per bet when edge >= threshold
  - KellyStake: fractional Kelly sizing (prepared, not default)

Does NOT place real bets. Purely retrospective / analytical.
"""
from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.prediction.market_odds import MarketOdds
from app.db.models.prediction.prediction import Prediction
from app.db.models.prediction.prediction_eval import PredictionEval
from app.services.prediction.value_service import odds_to_probs, compute_edge

logger = logging.getLogger(__name__)


# ── Data containers ───────────────────────────────────────────────────────

@dataclass
class BetRecord:
    """Single simulated bet."""
    match_id: int
    utc_date: datetime | None
    outcome_bet: str          # "home" | "draw" | "away"
    stake: float
    odds: float
    edge: float
    p_model: float
    actual_outcome: str       # "HOME" | "DRAW" | "AWAY"
    profit: float             # +win or -stake
    bankroll_after: float


@dataclass
class SimulationResult:
    """Summary of a bankroll simulation run."""
    initial_bankroll: float
    final_bankroll: float
    roi: float                # (final - initial) / initial
    max_drawdown: float       # worst peak-to-trough decline
    total_bets: int
    wins: int
    win_rate: float
    bets: list[BetRecord] = field(default_factory=list)


# ── Strategy interface ────────────────────────────────────────────────────

class StakeStrategy(ABC):
    """Abstract staking strategy."""

    @abstractmethod
    def compute_stake(
        self,
        bankroll: float,
        edge: float,
        odds: float,
        p_model: float,
    ) -> float:
        """Return the stake amount for a bet. 0 = skip."""
        ...


class FlatStakeStrategy(StakeStrategy):
    """Fixed stake per qualifying bet."""

    def __init__(self, stake_size: float) -> None:
        self.stake_size = stake_size

    def compute_stake(
        self,
        bankroll: float,
        edge: float,
        odds: float,
        p_model: float,
    ) -> float:
        if bankroll < self.stake_size:
            return 0.0
        return self.stake_size


class KellyStakeStrategy(StakeStrategy):
    """Fractional Kelly criterion staking.

    f* = (p * (O-1) - (1-p)) / (O-1)
    stake = fraction * f* * bankroll,  capped at max_pct * bankroll.

    Use fraction < 1.0 (e.g. 0.10) to reduce variance.
    Defaults are read from config.KELLY_FRACTION / config.MAX_STAKE_PERCENT.
    """

    def __init__(self, fraction: float | None = None, max_pct: float | None = None) -> None:
        from config import KELLY_FRACTION, MAX_STAKE_PERCENT
        self.fraction = fraction if fraction is not None else KELLY_FRACTION
        self.max_pct = max_pct if max_pct is not None else MAX_STAKE_PERCENT

    def compute_stake(
        self,
        bankroll: float,
        edge: float,
        odds: float,
        p_model: float,
    ) -> float:
        b = odds - 1.0
        if b <= 0:
            return 0.0
        kelly_f = (b * p_model - (1.0 - p_model)) / b
        if kelly_f <= 0:
            return 0.0
        stake = self.fraction * kelly_f * bankroll
        # Cap to max_pct of bankroll
        cap = self.max_pct * bankroll
        stake = min(stake, cap)
        return round(max(stake, 0.0), 2)


# ── Simulator ─────────────────────────────────────────────────────────────

class BankrollSimulator:
    """Retrospective bankroll simulation engine.

    Consumes evaluated predictions + market odds from the DB.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _load_simulation_data(self) -> list[dict]:
        """Load finished matches that have both a prediction, evaluation, AND market odds.

        Returns rows sorted chronologically, each containing model probs,
        actual outcome, and consensus market odds.
        """
        stmt = (
            select(
                Prediction.p_home,
                Prediction.p_draw,
                Prediction.p_away,
                Prediction.match_id,
                PredictionEval.actual_outcome,
                Match.utc_date,
                Match.id.label("mid"),
            )
            .join(PredictionEval, PredictionEval.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
            .where(Match.status.in_(("FINISHED", "FT")))
            .order_by(Match.utc_date.asc())
        )
        pred_rows = list(self.db.execute(stmt))
        if not pred_rows:
            return []

        # Batch-load consensus odds for involved matches
        match_ids = list({r.match_id for r in pred_rows})
        odds_map: dict[int, dict] = {}
        # Process in batches to avoid huge IN-clauses
        batch_size = 500
        for i in range(0, len(match_ids), batch_size):
            batch = match_ids[i:i + batch_size]
            odds_stmt = (
                select(MarketOdds.match_id, MarketOdds.home_odds, MarketOdds.draw_odds, MarketOdds.away_odds)
                .where(MarketOdds.match_id.in_(batch))
            )
            for orow in self.db.execute(odds_stmt):
                mid = orow.match_id
                if mid not in odds_map:
                    odds_map[mid] = {"home": [], "draw": [], "away": []}
                odds_map[mid]["home"].append(orow.home_odds)
                odds_map[mid]["draw"].append(orow.draw_odds)
                odds_map[mid]["away"].append(orow.away_odds)

        # Build final list with consensus odds
        results: list[dict] = []
        for r in pred_rows:
            if r.match_id not in odds_map:
                continue
            od = odds_map[r.match_id]
            n = len(od["home"])
            consensus_home = sum(od["home"]) / n
            consensus_draw = sum(od["draw"]) / n
            consensus_away = sum(od["away"]) / n
            results.append({
                "match_id": r.match_id,
                "utc_date": r.utc_date,
                "p_home": float(r.p_home),
                "p_draw": float(r.p_draw),
                "p_away": float(r.p_away),
                "actual_outcome": r.actual_outcome,
                "home_odds": consensus_home,
                "draw_odds": consensus_draw,
                "away_odds": consensus_away,
            })
        return results

    def simulate(
        self,
        initial_bankroll: float = 1000.0,
        min_edge: float = 0.03,
        strategy: StakeStrategy | None = None,
        max_bets: int | None = None,
    ) -> SimulationResult:
        """Run a full bankroll simulation.

        Args:
            initial_bankroll: Starting capital.
            min_edge: Minimum edge threshold to place a bet.
            strategy: Staking strategy (defaults to FlatStake at 1% bankroll).
            max_bets: Cap on number of bets (None = unlimited).
        """
        if strategy is None:
            strategy = FlatStakeStrategy(stake_size=round(initial_bankroll * 0.01, 2))

        data = self._load_simulation_data()
        bankroll = initial_bankroll
        peak = bankroll
        max_dd = 0.0
        bets: list[BetRecord] = []
        wins = 0

        for row in data:
            if max_bets is not None and len(bets) >= max_bets:
                break
            if bankroll <= 0:
                break

            # Compute edge for each outcome
            market = odds_to_probs(row["home_odds"], row["draw_odds"], row["away_odds"])
            model = {"p_home": row["p_home"], "p_draw": row["p_draw"], "p_away": row["p_away"]}
            edge = compute_edge(model, market)

            # Find the best edge outcome
            candidates = [
                ("home", edge["edge_home"], row["p_home"], row["home_odds"]),
                ("draw", edge["edge_draw"], row["p_draw"], row["draw_odds"]),
                ("away", edge["edge_away"], row["p_away"], row["away_odds"]),
            ]
            best = max(candidates, key=lambda x: x[1])
            outcome_bet, bet_edge, p_model, odds = best

            if bet_edge < min_edge:
                continue

            stake = strategy.compute_stake(bankroll, bet_edge, odds, p_model)
            if stake <= 0:
                continue
            # Ensure stake doesn't exceed bankroll
            stake = min(stake, bankroll)

            # Determine win/loss
            actual = row["actual_outcome"]  # "HOME", "DRAW", "AWAY"
            won = actual.lower() == outcome_bet.lower()
            if won:
                profit = stake * (odds - 1.0)
                wins += 1
            else:
                profit = -stake

            bankroll += profit
            bankroll = round(bankroll, 2)

            # Track max drawdown
            if bankroll > peak:
                peak = bankroll
            dd = (peak - bankroll) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

            bets.append(BetRecord(
                match_id=row["match_id"],
                utc_date=row["utc_date"],
                outcome_bet=outcome_bet,
                stake=round(stake, 2),
                odds=round(odds, 3),
                edge=round(bet_edge, 6),
                p_model=round(p_model, 6),
                actual_outcome=actual,
                profit=round(profit, 2),
                bankroll_after=bankroll,
            ))

        total = len(bets)
        roi = (bankroll - initial_bankroll) / initial_bankroll if initial_bankroll > 0 else 0.0

        return SimulationResult(
            initial_bankroll=initial_bankroll,
            final_bankroll=bankroll,
            roi=round(roi, 6),
            max_drawdown=round(max_dd, 6),
            total_bets=total,
            wins=wins,
            win_rate=round(wins / total, 6) if total > 0 else 0.0,
            bets=bets,
        )
