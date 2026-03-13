"""
Scheduler automático — ejecuta el pipeline completo en segundo plano.

Se integra con FastAPI via lifespan events.  Cuando arranca el servidor,
el scheduler se inicia y ejecuta los jobs en los intervalos configurados.

Jobs:
    1. sync_pipeline  (cada SCHEDULER_SYNC_HOURS h):
         sync fixtures (todas las ligas) → sync stats → pre-calcular predicciones
    2. retrain_pipeline (cada SCHEDULER_RETRAIN_HOURS h):
         rolling retrain (dataset completo) → backtest (para monitorear calidad)

Todas las frecuencias son configurables por .env.
Poner SCHEDULER_ENABLED=false para desactivarlo.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── configuración ─────────────────────────────────────────────────────────

SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes")
SYNC_INTERVAL_HOURS = float(os.getenv("SCHEDULER_SYNC_HOURS", "6"))
RETRAIN_INTERVAL_HOURS = float(os.getenv("SCHEDULER_RETRAIN_HOURS", "168"))  # 7 días
BACKFILL_INTERVAL_HOURS = float(os.getenv("SCHEDULER_BACKFILL_HOURS", "12"))

_scheduler: BackgroundScheduler | None = None

# Cross-job lock — prevents sync and retrain from running concurrently
# and stomping on each other's DB sessions.
_pipeline_lock = threading.Lock()


# ── jobs ──────────────────────────────────────────────────────────────────

def _run_sync_pipeline() -> None:
    """Sync fixtures + stats + predicciones para TODAS las ligas."""
    from app.worker_main import precompute_predictions, sync_fixtures, sync_match_stats

    if not _pipeline_lock.acquire(blocking=False):
        logger.warning("Scheduler: sync pipeline skipped — another pipeline is running")
        return

    try:
        logger.info("⏰ Scheduler: iniciando sync pipeline (todas las ligas)")
        try:
            sync_fixtures()
        except Exception:
            logger.exception("Scheduler: error en sync_fixtures")

        try:
            sync_match_stats()
        except Exception:
            logger.exception("Scheduler: error en sync_match_stats")

        try:
            precompute_predictions()
        except Exception:
            logger.exception("Scheduler: error en precompute_predictions")

        logger.info("✅ Scheduler: sync pipeline completado")
    finally:
        _pipeline_lock.release()


def _run_retrain_pipeline() -> None:
    """Rolling retrain + backtest sobre el dataset completo (todas las ligas)."""
    from app.db.session import SessionLocal
    from app.services.prediction.backtesting_service import BacktestingService
    from app.services.prediction.prediction_service import PredictionService
    from app.services.prediction.rolling_retrain_service import RollingRetrainService

    if not _pipeline_lock.acquire(blocking=False):
        logger.warning("Scheduler: retrain pipeline skipped — another pipeline is running")
        return

    try:
        logger.info("⏰ Scheduler: iniciando retrain pipeline (dataset completo)")

        # 1) Rolling retrain — league_id=None procesa TODAS las ligas
        db = SessionLocal()
        try:
            retrain_svc = RollingRetrainService(db=db, league_id=None, dry_run=False)
            retrain_report = retrain_svc.run()
            db.commit()
            logger.info("Retrain completado: %s", retrain_report.summary()[:200])
        except Exception:
            db.rollback()
            logger.exception("Scheduler: error en rolling retrain")
        finally:
            db.close()

        # 2) Backtest — league_id=None evalúa dataset completo
        db = SessionLocal()
        try:
            bt_svc = BacktestingService(db, league_id=None)
            bt_report = bt_svc.run()
            logger.info("Backtest completado: %s", bt_report.summary()[:200])
        except Exception:
            logger.exception("Scheduler: error en backtest")
        finally:
            db.close()

        # 3) Invalidar predicciones cacheadas tras retrain
        db = SessionLocal()
        try:
            svc = PredictionService(db)
            invalidated = svc.invalidate_stale_predictions()
            db.commit()
            if invalidated:
                logger.info("Predicciones invalidadas tras retrain: %d", invalidated)
        except Exception:
            db.rollback()
            logger.exception("Scheduler: error invalidando predicciones")
        finally:
            db.close()

        logger.info("✅ Scheduler: retrain pipeline completado")
    finally:
        _pipeline_lock.release()


def _run_backfill_pipeline() -> None:
    """Backfill historical stats for matches that lack them + purge old raw records."""
    from app.worker_main import backfill_stats

    logger.info("⏰ Scheduler: iniciando backfill de stats")
    try:
        backfill_stats()
    except Exception:
        logger.exception("Scheduler: error en backfill_stats")

    # Purge raw_records older than 90 days to prevent unbounded table growth
    try:
        from app.db.session import SessionLocal
        from app.repositories.core.raw_record_repository import RawRecordRepository

        db = SessionLocal()
        try:
            repo = RawRecordRepository(db)
            purged = repo.purge_older_than(days=90)
            db.commit()
            if purged:
                logger.info("Purged %d raw_records older than 90 days", purged)
        except Exception:
            db.rollback()
            logger.exception("Scheduler: error purging raw_records")
        finally:
            db.close()
    except Exception:
        logger.exception("Scheduler: error in raw_records purge setup")

    logger.info("✅ Scheduler: backfill completado")


# ── lifecycle ─────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Arranca el scheduler (llamado desde FastAPI lifespan)."""
    global _scheduler

    if not SCHEDULER_ENABLED:
        logger.info("Scheduler deshabilitado (SCHEDULER_ENABLED=false)")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Job 1: sync pipeline (delay 30s to let the server start cleanly)
    _initial_delay = int(os.getenv("SCHEDULER_INITIAL_DELAY_SECS", "30"))
    _scheduler.add_job(
        _run_sync_pipeline,
        "interval",
        hours=SYNC_INTERVAL_HOURS,
        id="sync_pipeline",
        name="Sync fixtures + stats + predicciones",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=_initial_delay),
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # Job 2: retrain pipeline
    _scheduler.add_job(
        _run_retrain_pipeline,
        "interval",
        hours=RETRAIN_INTERVAL_HOURS,
        id="retrain_pipeline",
        name="Rolling retrain + backtest",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # Job 3: backfill stats
    _scheduler.add_job(
        _run_backfill_pipeline,
        "interval",
        hours=BACKFILL_INTERVAL_HOURS,
        id="backfill_pipeline",
        name="Backfill historical stats",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    _scheduler.start()

    logger.info(
        "🚀 Scheduler iniciado — sync cada %.1fh, retrain cada %.1fh, backfill cada %.1fh",
        SYNC_INTERVAL_HOURS,
        RETRAIN_INTERVAL_HOURS,
        BACKFILL_INTERVAL_HOURS,
    )
    for job in _scheduler.get_jobs():
        logger.info("  Job '%s' próxima ejecución: %s", job.name, job.next_run_time)


def stop_scheduler() -> None:
    """Detiene el scheduler (llamado al cerrar la app)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido")
        _scheduler = None


def get_scheduler_status() -> dict:
    """Info del scheduler para el endpoint /health."""
    if _scheduler is None:
        return {"enabled": False}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    return {"enabled": True, "running": _scheduler.running, "jobs": jobs}
