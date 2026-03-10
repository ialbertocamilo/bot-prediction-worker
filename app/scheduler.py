"""
Scheduler automático — ejecuta el pipeline completo en segundo plano.

Se integra con FastAPI via lifespan events.  Cuando arranca el servidor,
el scheduler se inicia y ejecuta los jobs en los intervalos configurados.

Jobs:
    1. sync_pipeline  (cada SCHEDULER_SYNC_HOURS h):
         sync fixtures → sync stats → pre-calcular predicciones
    2. retrain_pipeline (cada SCHEDULER_RETRAIN_HOURS h):
         rolling retrain → backtest (para monitorear calidad)

Todas las frecuencias son configurables por .env.
Poner SCHEDULER_ENABLED=false para desactivarlo.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── configuración ─────────────────────────────────────────────────────────

SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes")
SYNC_INTERVAL_HOURS = float(os.getenv("SCHEDULER_SYNC_HOURS", "6"))
RETRAIN_INTERVAL_HOURS = float(os.getenv("SCHEDULER_RETRAIN_HOURS", "168"))  # 7 días
DEFAULT_LEAGUE_ID = os.getenv("DEFAULT_LEAGUE_ID")

_scheduler: BackgroundScheduler | None = None


# ── jobs ──────────────────────────────────────────────────────────────────

def _run_sync_pipeline() -> None:
    """Sync fixtures + stats + predicciones.  Se ejecuta periódicamente."""
    from app.worker_main import precompute_predictions, sync_fixtures, sync_match_stats

    logger.info("⏰ Scheduler: iniciando sync pipeline")
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


def _run_retrain_pipeline() -> None:
    """Rolling retrain + backtest.  Se ejecuta semanalmente."""
    from app.db.session import SessionLocal
    from app.services.prediction.backtesting_service import BacktestingService
    from app.services.prediction.rolling_retrain_service import RollingRetrainService

    league_id = int(DEFAULT_LEAGUE_ID) if DEFAULT_LEAGUE_ID else None

    logger.info("⏰ Scheduler: iniciando retrain pipeline (league_id=%s)", league_id)

    db = SessionLocal()
    try:
        # 1) Rolling retrain
        retrain_svc = RollingRetrainService(db=db, league_id=league_id, dry_run=False)
        retrain_report = retrain_svc.run()
        logger.info("Retrain completado: %s", retrain_report.summary()[:200])
    except Exception:
        logger.exception("Scheduler: error en rolling retrain")
    finally:
        db.close()

    db = SessionLocal()
    try:
        # 2) Backtest para monitorear calidad
        bt_svc = BacktestingService(db, league_id=league_id)
        bt_report = bt_svc.run()
        logger.info("Backtest completado: %s", bt_report.summary()[:200])
    except Exception:
        logger.exception("Scheduler: error en backtest")
    finally:
        db.close()

    logger.info("✅ Scheduler: retrain pipeline completado")


# ── lifecycle ─────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Arranca el scheduler (llamado desde FastAPI lifespan)."""
    global _scheduler

    if not SCHEDULER_ENABLED:
        logger.info("Scheduler deshabilitado (SCHEDULER_ENABLED=false)")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Job 1: sync pipeline
    _scheduler.add_job(
        _run_sync_pipeline,
        "interval",
        hours=SYNC_INTERVAL_HOURS,
        id="sync_pipeline",
        name="Sync fixtures + stats + predicciones",
        next_run_time=datetime.now(timezone.utc),  # ejecutar inmediatamente al inicio
        misfire_grace_time=3600,
    )

    # Job 2: retrain pipeline
    _scheduler.add_job(
        _run_retrain_pipeline,
        "interval",
        hours=RETRAIN_INTERVAL_HOURS,
        id="retrain_pipeline",
        name="Rolling retrain + backtest",
        misfire_grace_time=3600,
    )

    _scheduler.start()

    logger.info(
        "🚀 Scheduler iniciado — sync cada %.1fh, retrain cada %.1fh",
        SYNC_INTERVAL_HOURS,
        RETRAIN_INTERVAL_HOURS,
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
