"""
Model evaluation service — computes statistical quality metrics for the
Dixon-Coles prediction model using historical prediction_eval data.

Metrics:
  - Brier Score (global, per-league, per-season)
  - Log Loss / Cross-Entropy (global, per-league, per-season)
  - Calibration curve (binned predicted vs actual frequency)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.prediction.prediction import Prediction
from app.db.models.prediction.prediction_eval import PredictionEval

logger = logging.getLogger(__name__)

# Numerical floor to avoid log(0)
_EPS = 1e-15


@dataclass
class EvalMetrics:
    """Container for evaluation results."""
    brier_score: float
    log_loss: float
    samples: int


@dataclass
class CalibrationBin:
    """Single bin of a calibration curve."""
    bin_lower: float
    bin_upper: float
    avg_predicted: float
    actual_frequency: float
    count: int


class ModelEvaluationService:
    """Evaluates model quality from historical predictions vs outcomes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Core queries ──────────────────────────────────────────────────

    def _base_query(self):
        """Base query joining predictions ↔ prediction_eval ↔ matches."""
        return (
            select(
                Prediction.p_home,
                Prediction.p_draw,
                Prediction.p_away,
                PredictionEval.actual_outcome,
                Match.league_id,
                Match.season_id,
                Match.utc_date,
                Match.id.label("match_id"),
            )
            .join(PredictionEval, PredictionEval.prediction_id == Prediction.id)
            .join(Match, Match.id == Prediction.match_id)
        )

    def _fetch_rows(
        self,
        league_id: int | None = None,
        season_id: int | None = None,
    ) -> list:
        """Fetch evaluated prediction rows, optionally filtered."""
        stmt = self._base_query()
        if league_id is not None:
            stmt = stmt.where(Match.league_id == league_id)
        if season_id is not None:
            stmt = stmt.where(Match.season_id == season_id)
        stmt = stmt.order_by(Match.utc_date.asc())
        return list(self.db.execute(stmt))

    # ── Brier Score ───────────────────────────────────────────────────

    @staticmethod
    def _brier_single(p_home: float, p_draw: float, p_away: float, outcome: str) -> float:
        """Brier score for one 1X2 prediction."""
        y_h = 1.0 if outcome == "HOME" else 0.0
        y_d = 1.0 if outcome == "DRAW" else 0.0
        y_a = 1.0 if outcome == "AWAY" else 0.0
        return (p_home - y_h) ** 2 + (p_draw - y_d) ** 2 + (p_away - y_a) ** 2

    # ── Log Loss ──────────────────────────────────────────────────────

    @staticmethod
    def _logloss_single(p_home: float, p_draw: float, p_away: float, outcome: str) -> float:
        """Negative log-likelihood for one prediction. Uses clipping."""
        if outcome == "HOME":
            p = p_home
        elif outcome == "DRAW":
            p = p_draw
        else:
            p = p_away
        p = max(p, _EPS)
        p = min(p, 1.0 - _EPS)
        return -math.log(p)

    # ── Aggregate metrics ─────────────────────────────────────────────

    def _compute_metrics(self, rows: list) -> EvalMetrics | None:
        """Compute Brier + LogLoss over a set of rows."""
        if not rows:
            return None
        total_brier = 0.0
        total_ll = 0.0
        n = 0
        for r in rows:
            p_home, p_draw, p_away = float(r.p_home), float(r.p_draw), float(r.p_away)
            outcome = r.actual_outcome
            if outcome not in ("HOME", "DRAW", "AWAY"):
                continue
            total_brier += self._brier_single(p_home, p_draw, p_away, outcome)
            total_ll += self._logloss_single(p_home, p_draw, p_away, outcome)
            n += 1
        if n == 0:
            return None
        return EvalMetrics(
            brier_score=round(total_brier / n, 6),
            log_loss=round(total_ll / n, 6),
            samples=n,
        )

    # ── Public API ────────────────────────────────────────────────────

    def global_metrics(self) -> EvalMetrics | None:
        """Overall model quality metrics."""
        return self._compute_metrics(self._fetch_rows())

    def metrics_by_league(self) -> dict[int, EvalMetrics]:
        """Brier + LogLoss grouped by league_id."""
        rows = self._fetch_rows()
        grouped: dict[int, list] = {}
        for r in rows:
            grouped.setdefault(r.league_id, []).append(r)
        out: dict[int, EvalMetrics] = {}
        for lid, group in grouped.items():
            m = self._compute_metrics(group)
            if m:
                out[lid] = m
        return out

    def metrics_by_season(self) -> dict[int, EvalMetrics]:
        """Brier + LogLoss grouped by season_id."""
        rows = self._fetch_rows()
        grouped: dict[int, list] = {}
        for r in rows:
            if r.season_id is not None:
                grouped.setdefault(r.season_id, []).append(r)
        out: dict[int, EvalMetrics] = {}
        for sid, group in grouped.items():
            m = self._compute_metrics(group)
            if m:
                out[sid] = m
        return out

    # ── Calibration curve ─────────────────────────────────────────────

    def calibration_curve(self, bins: int = 10) -> list[CalibrationBin]:
        """Compute calibration curve data for all three 1X2 outcomes pooled.

        Each prediction contributes three data-points:
          (p_home, was_home), (p_draw, was_draw), (p_away, was_away)
        grouped into equal-width bins.
        """
        rows = self._fetch_rows()
        if not rows:
            return []

        # Collect (predicted_prob, actual_binary) tuples
        points: list[tuple[float, float]] = []
        for r in rows:
            outcome = r.actual_outcome
            if outcome not in ("HOME", "DRAW", "AWAY"):
                continue
            p_home, p_draw, p_away = float(r.p_home), float(r.p_draw), float(r.p_away)
            points.append((p_home, 1.0 if outcome == "HOME" else 0.0))
            points.append((p_draw, 1.0 if outcome == "DRAW" else 0.0))
            points.append((p_away, 1.0 if outcome == "AWAY" else 0.0))

        if not points:
            return []

        bin_width = 1.0 / bins
        result: list[CalibrationBin] = []
        for i in range(bins):
            lo = i * bin_width
            hi = (i + 1) * bin_width
            in_bin = [(p, a) for p, a in points if lo <= p < hi or (i == bins - 1 and p == hi)]
            if not in_bin:
                result.append(CalibrationBin(
                    bin_lower=round(lo, 4),
                    bin_upper=round(hi, 4),
                    avg_predicted=round((lo + hi) / 2, 4),
                    actual_frequency=0.0,
                    count=0,
                ))
                continue
            avg_pred = sum(p for p, _ in in_bin) / len(in_bin)
            actual_freq = sum(a for _, a in in_bin) / len(in_bin)
            result.append(CalibrationBin(
                bin_lower=round(lo, 4),
                bin_upper=round(hi, 4),
                avg_predicted=round(avg_pred, 6),
                actual_frequency=round(actual_freq, 6),
                count=len(in_bin),
            ))
        return result
