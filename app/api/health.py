from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.api.dependencies import get_db
from app.providers.cache import get_provider_cache
from app.providers.rate_limiter import get_all_metrics as get_rate_limiter_metrics
from app.scheduler import get_scheduler_status

router = APIRouter()


@router.get("")
def health():
    return {"status": "ok", "scheduler": get_scheduler_status()}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    """Observability endpoint: provider metrics, cache, stats coverage."""
    # Stats coverage
    total_finished = db.scalar(
        select(func.count(Match.id)).where(Match.status == "FINISHED")
    ) or 0
    matches_with_stats = db.scalar(
        select(func.count(func.distinct(MatchStats.match_id)))
    ) or 0
    total_scheduled = db.scalar(
        select(func.count(Match.id)).where(Match.status.in_(("SCHEDULED", "NS")))
    ) or 0

    coverage_pct = round(matches_with_stats / max(total_finished, 1) * 100, 1)

    return {
        "matches": {
            "finished": total_finished,
            "scheduled": total_scheduled,
            "with_stats": matches_with_stats,
            "stats_coverage_pct": coverage_pct,
        },
        "rate_limiters": get_rate_limiter_metrics(),
        "provider_cache": get_provider_cache().get_metrics(),
        "scheduler": get_scheduler_status(),
    }
