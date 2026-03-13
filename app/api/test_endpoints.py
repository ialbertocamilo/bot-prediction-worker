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

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.canonical_league_service import CanonicalLeagueService
from app.services.prediction.backtesting_service import BacktestingService
from app.services.prediction.hyperparameter_optimization_service import (
    HyperparameterOptimizationService,
)
from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.rolling_retrain_service import RollingRetrainService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request schemas (for POST endpoints) ─────────────────────────────────

class BacktestRequest(BaseModel):
    league_id: int = Field(..., description="League ID to backtest")
    from_date: str | None = Field(None, description="Start date YYYY-MM-DD (optional)")


class RollingRetrainRequest(BaseModel):
    league_id: int = Field(..., description="League ID")
    from_date: str | None = Field(None, description="Start date YYYY-MM-DD (optional)")
    dry_run: bool = Field(True, description="If true, no DB writes")


class OptimizeRequest(BaseModel):
    mode: Literal["coarse", "fine", "full", "random"] = Field("coarse", description="Search mode")
    league_id: int = Field(..., description="League ID")
    n_iter: int = Field(30, ge=1, le=500, description="Max random combos (only for 'random' mode)")


# ── League-keyed match cache (stateless-friendly) ────────────────────────

import time as _time

_MATCH_CACHE_TTL = 300  # 5 minutes


class _MatchCache:
    """League-keyed cache with lazy asyncio.Lock (avoids module-level deadlock).

    Each entry is keyed by canonical league index (None = all leagues) and
    stores {match_number → match_id} for fast lookup, plus a TTL timestamp.
    """

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._store: dict[int | None, tuple[float, dict[int, int]]] = {}

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create the lock inside the running event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def update(
        self, league_key: int | None, number_to_id: dict[int, int],
    ) -> None:
        async with self._get_lock():
            self._store[league_key] = (_time.monotonic(), dict(number_to_id))

    async def get_match_id(
        self, league_key: int | None, match_number: int,
    ) -> int | None:
        """Return match_id for 1-based match_number within a league listing."""
        async with self._get_lock():
            entry = self._store.get(league_key)
            if entry is None:
                return None
            ts, mapping = entry
            if _time.monotonic() - ts > _MATCH_CACHE_TTL:
                self._store.pop(league_key, None)
                return None
            return mapping.get(match_number)

    async def get_size(self, league_key: int | None) -> int:
        async with self._get_lock():
            entry = self._store.get(league_key)
            if entry is None:
                return 0
            ts, mapping = entry
            if _time.monotonic() - ts > _MATCH_CACHE_TTL:
                self._store.pop(league_key, None)
                return 0
            return len(mapping)


_match_cache = _MatchCache()


# ── GET /leagues ──────────────────────────────────────────────────────────

@router.get("/leagues")
def list_leagues(db: Session = Depends(get_db)):
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
async def list_matches(
    league: int | None = Query(None, description="Canonical league index (from /leagues)"),
    db: Session = Depends(get_db),
) -> dict:
    """Return upcoming scheduled matches (deduplicated across providers)."""
    svc = CanonicalLeagueService(db)

    # ── Validation (pure CPU — no threadpool needed) ──
    if league is not None:
        all_leagues = await run_in_threadpool(svc.list_leagues)
        if league < 1 or league > len(all_leagues):
            raise HTTPException(
                status_code=400,
                detail=f"league out of range. Valid: 1-{len(all_leagues)}",
            )
        await run_in_threadpool(svc.auto_ingest_if_empty, league)

    # ── Blocking I/O: only the DB query goes into the threadpool ──
    upcoming = await run_in_threadpool(svc.get_upcoming, canonical_index=league)

    # ── Response construction (pure CPU) ──
    items: list[dict] = []
    number_to_id: dict[int, int] = {}

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
        number_to_id[idx] = m.id

    # Cache keyed by league index — avoids cross-user overwrites
    await _match_cache.update(league, number_to_id)

    return {"count": len(items), "league": league, "matches": items}


# ── GET /predict ──────────────────────────────────────────────────────────

@router.get("/predict")
async def predict_match(
    match_id: int | None = Query(None, ge=1, description="Direct match ID (preferred, stateless)"),
    match_number: int | None = Query(None, ge=1, description="Number from /matches listing (legacy)"),
    league: int | None = Query(None, description="League index used in /matches (for cache lookup)"),
    db: Session = Depends(get_db),
) -> dict:
    """Predict a match.

    Preferred (stateless): ``/predict?match_id=42``
    Legacy shortcut:       ``/predict?match_number=3&league=1``
    """
    if match_id is None and match_number is None:
        raise HTTPException(
            status_code=400,
            detail="Provide match_id (preferred) or match_number + league.",
        )

    # Resolve match_id from cache if only match_number was given
    if match_id is None:
        cache_size = await _match_cache.get_size(league)
        if cache_size == 0:
            raise HTTPException(
                status_code=400,
                detail="No cached listing for this league. Call GET /matches?league=N first, or use match_id directly.",
            )
        match_id = await _match_cache.get_match_id(league, match_number)  # type: ignore[arg-type]
        if match_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"match_number out of range. Valid: 1-{cache_size}",
            )

    result = await run_in_threadpool(PredictionService(db).predict_match, match_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Could not generate prediction. Insufficient historical data.",
        )

    response = result.to_dict()
    if response.get("utc_date"):
        response["utc_date"] = response["utc_date"].isoformat()

    return {"match_id": match_id, "prediction": response}


# ── POST /backtest ────────────────────────────────────────────────────────

@router.post("/backtest")
async def run_backtest(req: BacktestRequest, db: Session = Depends(get_db)):
    """Run walk-forward backtesting (read-only, no persistence)."""
    svc = BacktestingService(db, league_id=req.league_id)
    report = await run_in_threadpool(svc.run)

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
async def run_rolling_retrain(req: RollingRetrainRequest, db: Session = Depends(get_db)):
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
    report = await run_in_threadpool(svc.run)

    # Invalidate cached predictions after actual retrain
    invalidated = 0
    if not req.dry_run:
        pred_svc = PredictionService(db)
        invalidated = pred_svc.invalidate_stale_predictions()

    return {
        "league_id": req.league_id,
        "dry_run": req.dry_run,
        "total_matches": report.total_matches,
        "processed": report.processed,
        "skipped_insufficient": report.skipped_insufficient,
        "skipped_existing": report.skipped_existing,
        "teams_updated": report.teams_updated,
        "params_clipped": report.params_clipped,
        "predictions_invalidated": invalidated,
    }


# ── POST /optimize_model ─────────────────────────────────────────────────

@router.post("/optimize_model")
async def run_optimize(req: OptimizeRequest, db: Session = Depends(get_db)):
    """Run hyperparameter search. Modes: coarse, fine, full, random."""
    svc = HyperparameterOptimizationService(db=db, league_id=req.league_id)

    def _run_search():
        if req.mode == "coarse":
            return svc.run_coarse()
        elif req.mode == "fine":
            return svc.run_fine()
        elif req.mode == "random":
            return svc.run_random(n_iter=req.n_iter)
        return svc.run()

    report = await run_in_threadpool(_run_search)

    best = report.best
    ranked = report.ranked[:10]

    # Invalidate cached predictions after optimization
    pred_svc = PredictionService(db)
    invalidated = pred_svc.invalidate_stale_predictions()

    return {
        "league_id": req.league_id,
        "mode": req.mode,
        "total_combos": report.total_combos,
        "evaluated": report.evaluated,
        "pruned": report.pruned,
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
        "predictions_invalidated": invalidated,
    }


# ── POST /seed_leagues ───────────────────────────────────────────────────

class SeedRequest(BaseModel):
    league_key: str | None = Field(None, description="Single league key, or null for all")
    days_back: int = Field(180, description="Days of historical data to ingest")
    days_ahead: int = Field(30, description="Days of upcoming fixtures to ingest")


@router.post("/seed_leagues")
def seed_leagues(req: SeedRequest, db: Session = Depends(get_db)):
    """Ingest results + fixtures for configured leagues from ESPN."""
    svc = CanonicalLeagueService(db)

    if req.league_key:
        n = svc.ingest_league(req.league_key, days_back=req.days_back, days_ahead=req.days_ahead)
        ingested = {req.league_key: n}
    else:
        total = svc.seed_all_leagues(days_back=req.days_back, days_ahead=req.days_ahead)
        ingested = {"_total": total}

    # Fresh summary
    svc2 = CanonicalLeagueService(db)
    summary = [
        {
            "index": lg.index,
            "key": lg.key,
            "name": lg.display_name,
            "finished": lg.finished_matches,
            "scheduled": lg.scheduled_matches,
            "db_league_ids": lg.db_league_ids,
        }
        for lg in svc2.list_leagues()
    ]

    return {"ingested": ingested, "leagues": summary}
