"""
API endpoints — read-only HTTP interface mirroring bot commands + pipeline tools.

GET  /leagues              — List canonical (deduplicated) leagues
GET  /matches?league=N     — Upcoming scheduled matches (optional canonical league filter)
GET  /predict?match_number=N — Prediction for match N from /matches listing

POST /backtest             — Walk-forward backtesting
POST /rolling_retrain      — Rolling retraining
POST /optimize_model       — Hyperparameter grid search

These endpoints do NOT modify production services or their logic.
They call existing services as-is and return results as JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.canonical_league_service import CanonicalLeagueService
from app.services.prediction.backtesting_service import BacktestingService
from app.services.prediction.hyperparameter_optimization_service import (
    HyperparameterOptimizationService,
)
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.rolling_retrain_service import RollingRetrainService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Request schemas (for POST endpoints) ─────────────────────────────────

class BacktestRequest(BaseModel):
    league_id: int = Field(..., description="League ID to backtest")
    from_date: str | None = Field(None, description="Start date YYYY-MM-DD (optional)")


class RollingRetrainRequest(BaseModel):
    league_id: int = Field(..., description="League ID")
    from_date: str | None = Field(None, description="Start date YYYY-MM-DD (optional)")
    dry_run: bool = Field(True, description="If true, no DB writes")


class OptimizeRequest(BaseModel):
    mode: Literal["coarse", "fine", "full"] = Field("coarse", description="Grid search mode")
    league_id: int = Field(..., description="League ID")


# ── Shared state for match listing ────────────────────────────────────────

_upcoming_cache: list[dict] = []
_upcoming_match_ids: list[int] = []


# ── GET /leagues ──────────────────────────────────────────────────────────

@router.get("/leagues")
def list_leagues(db: Session = Depends(_get_db)):
    """Return canonical (deduplicated) leagues with match counts."""
    svc = CanonicalLeagueService(db)
    leagues = svc.list_leagues()

    items = []
    for lg in leagues:
        if lg.scheduled_matches == 0:
            logger.warning(
                "Auditoria: liga '%s' (index=%d) sin partidos programados",
                lg.display_name, lg.index,
            )
        items.append({
            "index": lg.index,
            "key": lg.key,
            "name": lg.display_name,
            "country": lg.country,
            "db_league_ids": lg.db_league_ids,
            "finished_matches": lg.finished_matches,
            "scheduled_matches": lg.scheduled_matches,
        })

    return {"count": len(items), "leagues": items}


# ── GET /matches ──────────────────────────────────────────────────────────

@router.get("/matches")
def list_matches(
    league: int | None = Query(None, description="Canonical league index (from /leagues)"),
    db: Session = Depends(_get_db),
):
    """Return upcoming scheduled matches (deduplicated across providers)."""
    global _upcoming_cache, _upcoming_match_ids

    svc = CanonicalLeagueService(db)

    # Auto-ingest if the selected canonical league has no matches
    if league is not None:
        all_leagues = svc.list_leagues()
        if league < 1 or league > len(all_leagues):
            raise HTTPException(
                status_code=400,
                detail=f"league out of range. Valid: 1-{len(all_leagues)}",
            )
        svc.auto_ingest_if_empty(league)

    upcoming = svc.get_upcoming(canonical_index=league)

    items = []
    match_ids = []
    for idx, m in enumerate(upcoming[:30], 1):
        items.append({
            "number": idx,
            "match_id": m.id,
            "home_team": m.home_team.name if m.home_team else None,
            "away_team": m.away_team.name if m.away_team else None,
            "league": svc.display_name_for(m.league_id),
            "league_id": m.league_id,
            "utc_date": m.utc_date.isoformat() if m.utc_date else None,
            "round": m.round,
        })
        match_ids.append(m.id)

    _upcoming_cache = items
    _upcoming_match_ids = match_ids

    return {"count": len(items), "league": league, "matches": items}


# ── GET /predict ──────────────────────────────────────────────────────────

@router.get("/predict")
def predict_match(
    match_number: int = Query(..., ge=1, description="Number from /matches listing"),
    db: Session = Depends(_get_db),
):
    """Predict a match by its number from the /matches listing."""
    if not _upcoming_match_ids:
        raise HTTPException(
            status_code=400,
            detail="No match listing available. Call GET /matches first.",
        )
    if match_number > len(_upcoming_match_ids):
        raise HTTPException(
            status_code=400,
            detail=f"match_number out of range. Valid: 1-{len(_upcoming_match_ids)}",
        )

    match_id = _upcoming_match_ids[match_number - 1]
    service = PredictionService(db)
    result = service.predict_match(match_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Could not generate prediction. Insufficient historical data.",
        )

    # Sanitize non-serializable fields
    if result.get("utc_date"):
        result["utc_date"] = result["utc_date"].isoformat()

    return {"match_number": match_number, "match_id": match_id, "prediction": result}


# ── POST /backtest ────────────────────────────────────────────────────────

@router.post("/backtest")
def run_backtest(req: BacktestRequest, db: Session = Depends(_get_db)):
    """Run walk-forward backtesting (read-only, no persistence)."""
    svc = BacktestingService(db, league_id=req.league_id)
    report = svc.run()

    if report.total_matches == 0:
        raise HTTPException(
            status_code=404,
            detail="No matches evaluated. Check league_id or data availability.",
        )

    return {
        "league_id": req.league_id,
        "total_matches": report.total_matches,
        "skipped_matches": report.skipped_matches,
        "log_loss": round(report.log_loss, 4),
        "brier_score": round(report.brier_score, 4),
        "accuracy": round(report.accuracy, 4),
        "correct": report.correct,
        "calibration": {
            label: {"predicted_avg": round(pavg, 3), "actual_freq": round(freq, 3), "count": cnt}
            for label, (pavg, freq, cnt) in sorted(report.calibration.items())
        },
    }


# ── POST /rolling_retrain ────────────────────────────────────────────────

@router.post("/rolling_retrain")
def run_rolling_retrain(req: RollingRetrainRequest, db: Session = Depends(_get_db)):
    """Run rolling retraining. Defaults to dry_run=true for safety."""
    from_dt = None
    if req.from_date:
        try:
            from_dt = datetime.strptime(req.from_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date. Use YYYY-MM-DD.")

    svc = RollingRetrainService(
        db=db,
        league_id=req.league_id,
        from_date=from_dt,
        dry_run=req.dry_run,
    )
    report = svc.run()

    return {
        "league_id": req.league_id,
        "dry_run": req.dry_run,
        "total_matches": report.total_matches,
        "processed": report.processed,
        "skipped_insufficient": report.skipped_insufficient,
        "skipped_existing": report.skipped_existing,
        "teams_updated": report.teams_updated,
        "params_clipped": report.params_clipped,
    }


# ── POST /optimize_model ─────────────────────────────────────────────────

@router.post("/optimize_model")
def run_optimize(req: OptimizeRequest, db: Session = Depends(_get_db)):
    """Run hyperparameter grid search. Can be slow for 'full' mode."""
    svc = HyperparameterOptimizationService(db=db, league_id=req.league_id)

    if req.mode == "coarse":
        report = svc.run_coarse()
    elif req.mode == "fine":
        report = svc.run_fine()
    else:
        report = svc.run()

    best = report.best
    ranked = report.ranked[:10]

    return {
        "league_id": req.league_id,
        "mode": req.mode,
        "total_combos": report.total_combos,
        "evaluated": len(report.results),
        "total_elapsed_secs": round(report.total_elapsed_secs, 1),
        "best": {
            "time_decay": best.time_decay,
            "xg_weight": best.xg_weight,
            "home_adv": best.home_adv,
            "log_loss": round(best.log_loss, 4),
            "brier_score": round(best.brier_score, 4),
            "accuracy": round(best.accuracy, 4),
        } if best else None,
        "top_10": [
            {
                "rank": i,
                "time_decay": r.time_decay,
                "xg_weight": r.xg_weight,
                "home_adv": r.home_adv,
                "log_loss": round(r.log_loss, 4),
                "brier_score": round(r.brier_score, 4),
                "accuracy": round(r.accuracy, 4),
                "total_matches": r.total_matches,
            }
            for i, r in enumerate(ranked, 1)
        ],
    }
