"""
Worker — sincroniza datos desde providers y pre-calcula predicciones.

Uso:
    python -m app.worker_main                    # sync + predict
    python -m app.worker_main --sync             # solo sincronizar partidos
    python -m app.worker_main --predict          # solo predicciones
    python -m app.worker_main --sync-players     # sincronizar jugadores (SofaScore)
    python -m app.worker_main --sync-stats       # sincronizar stats (SofaScore)
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from dotenv import load_dotenv
from sqlalchemy import select

from app.db.models.football.match import Match
from app.db.session import SessionLocal
from app.providers.factory import ProviderFactory
from app.providers.sofascore.client import SofaScoreClient
from app.repositories.football.match_stats_repository import MatchStatsRepository
from app.repositories.football.team_repository import TeamRepository
from app.services.ingest.match_ingest_service import MatchIngestService
from app.services.ingest.match_stats_ingest_service import MatchStatsIngestService
from app.services.ingest.player_ingest_service import PlayerIngestService
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


# ── sync players ──────────────────────────────────────────────────────────

def sync_players() -> None:
    """Sync players from SofaScore lineups into DB."""
    league_id = int(os.getenv("DEFAULT_LEAGUE_ID", "670"))
    season = int(os.getenv("DEFAULT_SEASON", "2026"))

    provider = ProviderFactory.create("sofascore")
    players = provider.get_players(league_id=league_id, season=season)
    logger.info("SofaScore devolvió %d jugadores", len(players))

    if not players:
        return

    db = SessionLocal()
    try:
        svc = PlayerIngestService(db)
        ids = svc.ingest_players(players)
        db.commit()
        logger.info("Jugadores ingresados/actualizados: %d", len(ids))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── sync match stats ─────────────────────────────────────────────────────

def _match_team_name(name_a: str, name_b: str) -> bool:
    """Fuzzy match team names (exact first, then ratio > 0.8)."""
    a, b = name_a.lower().strip(), name_b.lower().strip()
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.8


def sync_match_stats() -> None:
    """Fetch stats from SofaScore for finished DB matches that lack stats."""
    sofascore = ProviderFactory.create("sofascore")

    db = SessionLocal()
    try:
        stats_repo = MatchStatsRepository(db)
        team_repo = TeamRepository(db)
        stats_svc = MatchStatsIngestService(db)

        # 1) Find ALL finished matches without stats
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.desc())
        )
        finished = list(db.scalars(stmt).all())
        needing_stats: list[Match] = [
            m for m in finished if not stats_repo.list_by_match(m.id)
        ]
        logger.info(
            "Partidos terminados sin stats: %d / %d",
            len(needing_stats), len(finished),
        )
        if not needing_stats:
            return

        # 2) Build lookup: (home_team_name, away_team_name) -> DB match
        team_cache: dict[int, str] = {}
        def _team_name(tid: int) -> str:
            if tid not in team_cache:
                t = team_repo.get_by_id(tid)
                team_cache[tid] = t.name if t else "Unknown"
            return team_cache[tid]

        pending: dict[int, tuple[str, str]] = {}
        for m in needing_stats:
            pending[m.id] = (_team_name(m.home_team_id), _team_name(m.away_team_id))

        # 3) Iterate SofaScore finished events, match to DB, fetch stats
        client = SofaScoreClient()
        page, max_pages, ingested = 0, 10, 0

        while page < max_pages and pending:
            try:
                data = client.get_tournament_events(page=page, direction="last")
            except Exception:
                logger.exception("Error obteniendo eventos SofaScore página %d", page)
                break

            events = data.get("events", [])
            if not events:
                break

            for event in events:
                if not pending:
                    break
                if event.get("status", {}).get("type", "") != "finished":
                    continue

                event_id = str(event["id"])
                sc_home = event.get("homeTeam", {}).get("name", "")
                sc_away = event.get("awayTeam", {}).get("name", "")

                # Find matching DB match
                matched_id: int | None = None
                for mid, (db_home, db_away) in pending.items():
                    if _match_team_name(sc_home, db_home) and _match_team_name(sc_away, db_away):
                        matched_id = mid
                        break

                if matched_id is None:
                    continue

                # Fetch stats from SofaScore
                stats_list = sofascore.get_match_stats(event_id)
                if not stats_list:
                    pending.pop(matched_id, None)
                    continue

                # Ingest with direct id mapping
                ids = stats_svc.ingest_match_stats(
                    stats_list,
                    source_match_id_to_db_id={event_id: matched_id},
                )
                ingested += len(ids)
                pending.pop(matched_id, None)

                logger.info(
                    "Stats event %s → match %d (%s vs %s): %d registros",
                    event_id, matched_id, sc_home, sc_away, len(ids),
                )

            if len(events) < 30:
                break
            page += 1

        db.commit()
        logger.info("Total stats ingresados: %d registros", ingested)
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

    # Individual flags
    run_sync = "--sync" in args
    run_predict = "--predict" in args
    run_sync_players = "--sync-players" in args
    run_sync_stats = "--sync-stats" in args

    # No flags → run sync + predict (original behaviour)
    if not any([run_sync, run_predict, run_sync_players, run_sync_stats]):
        run_sync = True
        run_predict = True

    if run_sync:
        sync_fixtures()
    if run_sync_players:
        sync_players()
    if run_sync_stats:
        sync_match_stats()
    if run_predict:
        precompute_predictions()


if __name__ == "__main__":
    main()
