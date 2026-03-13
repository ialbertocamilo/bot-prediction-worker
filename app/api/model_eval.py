"""
Model evaluation & bankroll simulation API endpoints.

Provides:
  GET /model/evaluation      — Brier score, log loss, sample count
  GET /model/calibration     — Calibration curve bins
  GET /model/bankroll-simulation — Hypothetical flat-stake simulation
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.prediction.model_evaluation_service import ModelEvaluationService
from app.services.prediction.bankroll_simulator import BankrollSimulator, FlatStakeStrategy

router = APIRouter()


@router.get("/evaluation")
def model_evaluation(
    league_id: int | None = Query(None, description="Filtrar por liga"),
    season_id: int | None = Query(None, description="Filtrar por temporada"),
    db: Session = Depends(get_db),
):
    """Model quality metrics: Brier Score & Log Loss.

    Without filters: returns global + per-league + per-season breakdowns.
    With league_id or season_id: returns filtered metrics only.
    """
    svc = ModelEvaluationService(db)

    # Filtered request
    if league_id is not None or season_id is not None:
        rows = svc._fetch_rows(league_id=league_id, season_id=season_id)
        m = svc._compute_metrics(rows)
        if m is None:
            return {"brier_score": None, "log_loss": None, "samples_evaluated": 0}
        return {
            "brier_score": m.brier_score,
            "log_loss": m.log_loss,
            "samples_evaluated": m.samples,
        }

    # Full breakdown
    global_m = svc.global_metrics()
    by_league = svc.metrics_by_league()
    by_season = svc.metrics_by_season()

    return {
        "global": {
            "brier_score": global_m.brier_score if global_m else None,
            "log_loss": global_m.log_loss if global_m else None,
            "samples_evaluated": global_m.samples if global_m else 0,
        },
        "by_league": {
            str(lid): asdict(m) for lid, m in by_league.items()
        },
        "by_season": {
            str(sid): asdict(m) for sid, m in by_season.items()
        },
    }


@router.get("/calibration")
def model_calibration(
    bins: int = Query(10, ge=2, le=50, description="Número de bins"),
    db: Session = Depends(get_db),
):
    """Calibration curve data: predicted probability vs actual frequency per bin."""
    svc = ModelEvaluationService(db)
    curve = svc.calibration_curve(bins=bins)
    return {
        "bins": bins,
        "data": [asdict(b) for b in curve],
    }


@router.get("/bankroll-simulation")
def bankroll_simulation(
    initial_bankroll: float = Query(1000.0, ge=1.0, description="Capital inicial"),
    stake_size: float = Query(10.0, ge=0.01, description="Stake fijo por apuesta"),
    min_edge: float = Query(0.03, ge=0.0, le=1.0, description="Edge mínimo para apostar"),
    max_bets: int | None = Query(None, ge=1, description="Máximo de apuestas"),
    db: Session = Depends(get_db),
):
    """Hypothetical bankroll simulation using flat-stake strategy.

    Uses historical predictions + market odds to simulate betting.
    Does NOT place real bets.
    """
    sim = BankrollSimulator(db)
    strategy = FlatStakeStrategy(stake_size=stake_size)
    result = sim.simulate(
        initial_bankroll=initial_bankroll,
        min_edge=min_edge,
        strategy=strategy,
        max_bets=max_bets,
    )
    return {
        "initial_bankroll": result.initial_bankroll,
        "final_bankroll": result.final_bankroll,
        "roi": result.roi,
        "max_drawdown": result.max_drawdown,
        "total_bets": result.total_bets,
        "wins": result.wins,
        "win_rate": result.win_rate,
    }
