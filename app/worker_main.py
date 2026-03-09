"""
Worker — sincroniza datos desde providers y pre-calcula predicciones.

Uso:
    python -m app.worker_main             # sync + predict
    python -m app.worker_main --sync      # solo sincronizar
    python -m app.worker_main --predict   # solo predicciones
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import select

from app.db.models.football.match import Match
from app.db.session import SessionLocal
from app.providers.factory import ProviderFactory
from app.services.ingest.match_ingest_service import MatchIngestService
from app.services.prediction.prediction_service import PredictionService

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── sync ──────────────────────────────────────────────────────────────────

def sync_fixtures() -> None:
    provider_name = os.getenv("ACTIVE_PROVIDER", "api-football")
    league_id_raw = os.getenv("DEFAULT_LEAGUE_ID")
    season_raw = os.getenv("DEFAULT_SEASON")

    if not league_id_raw or not season_raw:
        logger.error("DEFAULT_LEAGUE_ID y DEFAULT_SEASON requeridos en .env")
        return

    league_id = int(league_id_raw)
    season = int(season_raw)
    provider = ProviderFactory.create(provider_name)

    # Para temporadas históricas, usar el rango completo del año de la temporada.
    # Para la temporada actual, usar ventana relativa a hoy.
    current_year = date.today().year
    if season < current_year:
        # Temporada histórica: traer todo el año
        d_from = date(season, 1, 1)
        d_to = date(season, 12, 31)
    else:
        days_back = int(os.getenv("SYNC_DAYS_BACK", "60"))
        days_ahead = int(os.getenv("SYNC_DAYS_AHEAD", "14"))
        d_from = date.today() - timedelta(days=days_back)
        d_to = date.today() + timedelta(days=days_ahead)

    logger.info(
        "Sync %s  liga=%d  season=%d  %s → %s",
        provider_name, league_id, season, d_from, d_to,
    )

    results = provider.get_results(
        league_id=league_id, season=season, date_from=d_from, date_to=d_to,
    )
    fixtures = provider.get_fixtures(
        league_id=league_id, season=season, date_from=d_from, date_to=d_to,
    )
    all_matches = results + fixtures
    logger.info("Recibidos %d partidos del proveedor", len(all_matches))

    db = SessionLocal()
    try:
        svc = MatchIngestService(db)
        ids = svc.ingest_matches(all_matches)
        db.commit()
        logger.info("Ingresados %d partidos", len(ids))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── predict ───────────────────────────────────────────────────────────────

def precompute_predictions() -> None:
    db = SessionLocal()
    try:
        stmt = (
            select(Match)
            .where(Match.status == "SCHEDULED")
            .where(Match.utc_date >= datetime.now(timezone.utc))
            .order_by(Match.utc_date.asc())
            .limit(50)
        )
        upcoming = list(db.scalars(stmt).all())
        logger.info("Partidos próximos: %d", len(upcoming))

        svc = PredictionService(db)
        ok = 0
        for match in upcoming:
            try:
                result = svc.predict_match(match.id)
                if result:
                    ok += 1
                    logger.info(
                        "Predicción match %d: %s vs %s  →  H=%.1f%%  D=%.1f%%  A=%.1f%%",
                        match.id,
                        result["home_team"],
                        result["away_team"],
                        result["p_home"] * 100,
                        result["p_draw"] * 100,
                        result["p_away"] * 100,
                    )
            except Exception as exc:
                logger.warning("Error predicción match %d: %s", match.id, exc)

        logger.info("Predicciones generadas: %d / %d", ok, len(upcoming))
    finally:
        db.close()


# ── entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    run_sync = "--sync" in args or not args
    run_predict = "--predict" in args or not args

    if run_sync:
        sync_fixtures()
    if run_predict:
        precompute_predictions()


if __name__ == "__main__":
    main()
