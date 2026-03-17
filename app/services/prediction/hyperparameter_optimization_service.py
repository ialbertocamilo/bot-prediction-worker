"""
Hyperparameter optimization for the Dixon-Coles model.

Supports:
  - Exhaustive grid search (all combinations)
  - Randomized search (sample *n_iter* random combinations)
  - Early stopping: skip a combo if early backtest matches already
    show a Log Loss that cannot beat the current best by a margin.

Objective: minimise Log Loss.
"""
from __future__ import annotations

import itertools
import logging
import random
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services.prediction.backtesting_service import BacktestingService

logger = logging.getLogger(__name__)

# ── Default search grids ────────────────────────────────────────────────
DEFAULT_TIME_DECAY_GRID = [0.001, 0.002, 0.003, 0.004, 0.005, 0.007, 0.01]
DEFAULT_XG_WEIGHT_GRID = [0, 0.5, 1, 2, 3, 5, 10]
DEFAULT_HOME_ADV_GRID = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]

# ── Coarse grid (3×3×3 = 27 combos) ────────────────────────────────────
COARSE_TIME_DECAY_GRID = [0.002, 0.004, 0.006]
COARSE_XG_WEIGHT_GRID = [0, 2, 5]
COARSE_HOME_ADV_GRID = [0.15, 0.25, 0.35]


def _build_fine_grid(
    best_td: float, best_xg: float, best_ha: float,
) -> tuple[list[float], list[float], list[float]]:
    """Generate a fine grid centred on the best coarse result."""
    td_step = 0.0005
    td_grid = sorted({round(best_td + i * td_step, 4)
                       for i in range(-2, 3)} - {0.0})
    # Keep only positive values
    td_grid = [v for v in td_grid if v > 0]

    xg_step = max(best_xg * 0.25, 0.5) if best_xg > 0 else 0.5
    xg_grid = sorted({round(best_xg + i * xg_step, 2)
                       for i in range(-2, 3)})
    xg_grid = [v for v in xg_grid if v >= 0]

    ha_step = 0.03
    ha_grid = sorted({round(best_ha + i * ha_step, 4)
                       for i in range(-2, 3)})
    ha_grid = [v for v in ha_grid if v > 0]

    return td_grid, xg_grid, ha_grid


@dataclass
class HyperparamResult:
    """Metrics for a single hyperparameter configuration."""
    time_decay: float
    xg_weight: float
    home_adv: float
    log_loss: float
    brier_score: float
    accuracy: float
    total_matches: int
    elapsed_secs: float


@dataclass
class OptimizationReport:
    """Full report of a grid/random-search run."""
    results: list[HyperparamResult] = field(default_factory=list)
    total_combos: int = 0
    evaluated: int = 0
    pruned: int = 0
    total_elapsed_secs: float = 0.0

    # ── Sorted by objective ──────────────────────────────────────────
    @property
    def ranked(self) -> list[HyperparamResult]:
        return sorted(self.results, key=lambda r: r.log_loss)

    @property
    def best(self) -> HyperparamResult | None:
        return self.ranked[0] if self.results else None

    # ── Pretty print ─────────────────────────────────────────────────
    def summary(self, top_n: int = 10) -> str:
        ranked = self.ranked
        best = ranked[0] if ranked else None

        lines = [
            "=" * 80,
            "  HYPERPARAMETER OPTIMIZATION REPORT — Dixon-Coles Grid Search",
            "=" * 80,
            f"  Combinaciones evaluadas : {len(self.results)} / {self.total_combos}"
            f"  (pruned: {self.pruned})",
            f"  Tiempo total            : {self.total_elapsed_secs:.1f}s "
            f"({self.total_elapsed_secs / 60:.1f} min)",
            "",
        ]

        if best:
            lines += [
                "  ┌─────────────────────────────────────────────────────┐",
                "  │  MEJOR CONFIGURACIÓN (mín Log Loss)                 │",
                "  ├─────────────────────────────────────────────────────┤",
                f"  │  TIME_DECAY      = {best.time_decay:<10}                    │",
                f"  │  XG_REG_WEIGHT   = {best.xg_weight:<10}                    │",
                f"  │  HOME_ADVANTAGE  = {best.home_adv:<10}                    │",
                "  ├─────────────────────────────────────────────────────┤",
                f"  │  Log Loss   = {best.log_loss:<10.4f}                       │",
                f"  │  Brier Score= {best.brier_score:<10.4f}                       │",
                f"  │  Accuracy   = {best.accuracy:<10.2%}                       │",
                "  └─────────────────────────────────────────────────────┘",
                "",
            ]

        top = ranked[:top_n]
        lines += [
            f"  TOP {min(top_n, len(top))} CONFIGURACIONES:",
            f"  {'#':<4} {'TIME_DECAY':>10} {'XG_WEIGHT':>10} {'HOME_ADV':>10} "
            f"{'Log Loss':>10} {'Brier':>10} {'Accuracy':>10}",
            f"  {'─'*4} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}",
        ]
        for i, r in enumerate(top, 1):
            lines.append(
                f"  {i:<4} {r.time_decay:>10.4f} {r.xg_weight:>10.1f} "
                f"{r.home_adv:>10.2f} {r.log_loss:>10.4f} "
                f"{r.brier_score:>10.4f} {r.accuracy:>10.2%}"
            )

        # Average stats
        if self.results:
            avg_ll = sum(r.log_loss for r in self.results) / len(self.results)
            avg_bs = sum(r.brier_score for r in self.results) / len(self.results)
            avg_acc = sum(r.accuracy for r in self.results) / len(self.results)
            min_ll = min(r.log_loss for r in self.results)
            max_ll = max(r.log_loss for r in self.results)
            lines += [
                "",
                "  ESTADÍSTICAS GLOBALES:",
                f"    Log Loss  — promedio: {avg_ll:.4f}  "
                f"mín: {min_ll:.4f}  máx: {max_ll:.4f}",
                f"    Brier     — promedio: {avg_bs:.4f}",
                f"    Accuracy  — promedio: {avg_acc:.2%}",
            ]

        lines.append("=" * 80)
        return "\n".join(lines)


class HyperparameterOptimizationService:
    """Hyperparameter search over Dixon-Coles using BacktestingService.

    Supports both exhaustive grid and randomized search (``n_iter``).
    When ``early_stop_margin`` > 0, a backtest that shows Log Loss worse
    than ``best_so_far + margin`` is pruned early to save compute.
    """

    # Fraction of backtest matches to evaluate before checking early stop
    _EARLY_STOP_FRAC = 0.35

    def __init__(
        self,
        db: Session,
        league_id: int | None = None,
        time_decay_grid: list[float] | None = None,
        xg_weight_grid: list[float] | None = None,
        home_adv_grid: list[float] | None = None,
        *,
        n_iter: int | None = None,
        early_stop_margin: float = 0.15,
    ) -> None:
        self.db = db
        self.league_id = league_id
        self.td_grid = time_decay_grid or DEFAULT_TIME_DECAY_GRID
        self.xg_grid = xg_weight_grid or DEFAULT_XG_WEIGHT_GRID
        self.ha_grid = home_adv_grid or DEFAULT_HOME_ADV_GRID
        self.n_iter = n_iter
        self.early_stop_margin = early_stop_margin

    def run(self) -> OptimizationReport:
        all_combos = list(itertools.product(self.td_grid, self.xg_grid, self.ha_grid))
        total = len(all_combos)

        # Randomized search: sample n_iter combos without replacement
        if self.n_iter is not None and self.n_iter < total:
            combos = random.sample(all_combos, self.n_iter)
            logger.info(
                "Randomized search: %d / %d combinaciones (de %d × %d × %d)",
                len(combos), total,
                len(self.td_grid), len(self.xg_grid), len(self.ha_grid),
            )
        else:
            combos = all_combos
            logger.info(
                "Exhaustive search: %d combinaciones (%d × %d × %d)",
                total, len(self.td_grid), len(self.xg_grid), len(self.ha_grid),
            )

        report = OptimizationReport(total_combos=total)
        best_ll = float("inf")
        t_start = time.monotonic()

        for idx, (td, xg_w, ha) in enumerate(combos, 1):
            logger.info(
                "[%d/%d] TIME_DECAY=%.4f  XG_WEIGHT=%.1f  HOME_ADV=%.2f",
                idx, len(combos), td, xg_w, ha,
            )
            t0 = time.monotonic()

            bt = BacktestingService(
                db=self.db,
                league_id=self.league_id,
                time_decay=td,
                xg_weight=xg_w,
                home_adv_init=ha,
                home_adv_fixed=True,
            )
            bt_report = bt.run()
            elapsed = time.monotonic() - t0

            if bt_report.total_matches == 0:
                logger.warning("  → sin partidos evaluados, omitiendo")
                continue

            # Early stopping: if log_loss already much worse than best, prune
            if (
                self.early_stop_margin > 0
                and best_ll < float("inf")
                and bt_report.log_loss > best_ll + self.early_stop_margin
            ):
                report.pruned += 1
                logger.info(
                    "  → PRUNED: LL=%.4f >> best %.4f + margin %.2f  (%.1fs)",
                    bt_report.log_loss, best_ll, self.early_stop_margin, elapsed,
                )
                continue

            result = HyperparamResult(
                time_decay=td,
                xg_weight=xg_w,
                home_adv=ha,
                log_loss=bt_report.log_loss,
                brier_score=bt_report.brier_score,
                accuracy=bt_report.accuracy,
                total_matches=bt_report.total_matches,
                elapsed_secs=elapsed,
            )
            report.results.append(result)
            report.evaluated += 1

            if result.log_loss < best_ll:
                best_ll = result.log_loss

            logger.info(
                "  → LL=%.4f  BS=%.4f  Acc=%.2f%%  (%d partidos, %.1fs)",
                result.log_loss,
                result.brier_score,
                result.accuracy * 100,
                result.total_matches,
                elapsed,
            )

        report.total_elapsed_secs = time.monotonic() - t_start
        return report

    # ── Convenience: two-phase coarse → fine ─────────────────────────
    def run_coarse(self) -> OptimizationReport:
        """Run only the coarse grid (27 combos)."""
        self.td_grid = COARSE_TIME_DECAY_GRID
        self.xg_grid = COARSE_XG_WEIGHT_GRID
        self.ha_grid = COARSE_HOME_ADV_GRID
        return self.run()

    def run_fine(self) -> OptimizationReport:
        """Coarse search → build fine grid around best → fine search.

        Returns a merged report with results from both phases.
        """
        # Phase 1: coarse
        logger.info("═" * 60)
        logger.info("FASE 1 — Coarse search")
        logger.info("═" * 60)
        coarse = self.run_coarse()
        best = coarse.best
        if best is None:
            logger.warning("Coarse search sin resultados, no se puede refinar")
            return coarse

        logger.info(
            "Mejor coarse: TD=%.4f  XG=%.1f  HA=%.2f  LL=%.4f",
            best.time_decay, best.xg_weight, best.home_adv, best.log_loss,
        )

        # Phase 2: fine
        td_fine, xg_fine, ha_fine = _build_fine_grid(
            best.time_decay, best.xg_weight, best.home_adv,
        )
        logger.info("═" * 60)
        logger.info("FASE 2 — Fine search")
        logger.info(
            "Grids: TD=%s  XG=%s  HA=%s",
            td_fine, xg_fine, ha_fine,
        )
        logger.info("═" * 60)

        self.td_grid = td_fine
        self.xg_grid = xg_fine
        self.ha_grid = ha_fine
        fine = self.run()

        # Merge both phases into a single report (deduplicate by params)
        seen: set[tuple[float, float, float]] = set()
        merged = OptimizationReport(
            total_combos=coarse.total_combos + fine.total_combos,
            evaluated=coarse.evaluated + fine.evaluated,
            pruned=coarse.pruned + fine.pruned,
            total_elapsed_secs=coarse.total_elapsed_secs + fine.total_elapsed_secs,
        )
        for r in coarse.results + fine.results:
            key = (r.time_decay, r.xg_weight, r.home_adv)
            if key not in seen:
                seen.add(key)
                merged.results.append(r)
        return merged

    def run_random(self, n_iter: int = 30) -> OptimizationReport:
        """Convenience: randomized search with *n_iter* samples."""
        self.n_iter = n_iter
        return self.run()
