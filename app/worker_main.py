"""
Worker — sincroniza datos desde providers y pre-calcula predicciones.

Uso:
    python -m app.worker_main                    # sync + predict (todas las ligas)
    python -m app.worker_main --sync             # solo sincronizar partidos
    python -m app.worker_main --predict          # solo predicciones
    python -m app.worker_main --sync-players     # sincronizar jugadores
    python -m app.worker_main --sync-stats       # sincronizar stats
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from dotenv import load_dotenv
from sqlalchemy import select

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.db.session import SessionLocal
from app.providers.base import BaseProvider
from app.providers.factory import ProviderFactory
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

# ── cached stats provider ─────────────────────────────────────────────────

_stats_provider_instance: BaseProvider | None = None
_stats_provider_lock = threading.Lock()


def _get_stats_provider() -> BaseProvider:
    """Return a cached stats provider, creating it once on first call."""
    global _stats_provider_instance
    if _stats_provider_instance is None:
        with _stats_provider_lock:
            if _stats_provider_instance is None:
                name = os.getenv("STATS_PROVIDER", "sofascore")
                _stats_provider_instance = ProviderFactory.create(name)
                logger.info("Stats provider created (reusable): %s", name)
    return _stats_provider_instance


# ── sync ──────────────────────────────────────────────────────────────────

def sync_fixtures() -> None:
    """Sincroniza partidos para TODAS las ligas canónicas configuradas.

    Itera cada liga registrada en LEAGUE_GROUPS y ejecuta ingest
    desde el provider correspondiente. Si una liga falla, continúa
    con las demás.
    """
    from app.services.canonical_league_service import CanonicalLeagueService, LEAGUE_GROUPS

    days_back = int(os.getenv("SYNC_DAYS_BACK", "30"))
    days_ahead = int(os.getenv("SYNC_DAYS_AHEAD", "14"))

    db = SessionLocal()
    try:
        svc = CanonicalLeagueService(db)
        total = 0
        for i, group in enumerate(LEAGUE_GROUPS, 1):
            if not group.provider_name:
                continue
            try:
                logger.info(
                    "=== [%d/%d] Sync fixtures: %s ===",
                    i, len(LEAGUE_GROUPS), group.display_name,
                )
                n = svc._ingest_from_provider(
                    group, days_back=days_back, days_ahead=days_ahead,
                )
                total += n
                logger.info("%s: %d partidos sincronizados", group.display_name, n)
            except Exception:
                logger.exception(
                    "Error sincronizando %s — continuando con la siguiente liga",
                    group.display_name,
                )
        db.commit()
        logger.info("Sync fixtures completado: %d partidos total", total)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── sync players ──────────────────────────────────────────────────────────

def sync_players() -> None:
    """Sync players from the configured stats provider into DB."""
    league_id = int(os.getenv("DEFAULT_LEAGUE_ID", "670"))
    season = int(os.getenv("DEFAULT_SEASON", "2026"))

    provider = _get_stats_provider()
    players = provider.get_players(league_id=league_id, season=season)
    logger.info("%s devolvió %d jugadores", provider.provider_name, len(players))

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
    """Fetch stats from the configured stats provider for finished DB matches."""
    stats_provider = _get_stats_provider()

    db = SessionLocal()
    try:
        stats_svc = MatchStatsIngestService(db)

        # 1) Find ALL finished matches, then batch-check which have stats
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.desc())
        )
        finished = list(db.scalars(stmt).all())

        # Batch query: get all match_ids that already have stats
        finished_ids = [m.id for m in finished]
        has_stats: set[int] = set()
        batch_size = 500
        for start in range(0, len(finished_ids), batch_size):
            batch = finished_ids[start : start + batch_size]
            rows = db.execute(
                select(MatchStats.match_id)
                .where(MatchStats.match_id.in_(batch))
                .distinct()
            )
            has_stats.update(row.match_id for row in rows)

        needing_stats = [m for m in finished if m.id not in has_stats]
        logger.info(
            "Partidos terminados sin stats: %d / %d",
            len(needing_stats), len(finished),
        )
        if not needing_stats:
            return

        # 2) Team names are already eagerly loaded via lazy="joined"
        pending: dict[int, tuple[str, str]] = {}
        for m in needing_stats:
            home_name = m.home_team.name if m.home_team else "Unknown"
            away_name = m.away_team.name if m.away_team else "Unknown"
            pending[m.id] = (home_name, away_name)

        # 3) Group matches by date → query SofaScore date-based endpoint
        from collections import defaultdict

        matches_by_date: dict[date, list[int]] = defaultdict(list)
        match_dates: dict[int, date] = {}
        for m in needing_stats:
            if m.utc_date:
                d = m.utc_date.date() if hasattr(m.utc_date, 'date') else m.utc_date
                matches_by_date[d].append(m.id)
                match_dates[m.id] = d

        unique_dates = sorted(matches_by_date.keys(), reverse=True)
        logger.info("Stats sync: %d unique dates to query", len(unique_dates))

        ingested = 0
        has_get_events_for_date = hasattr(stats_provider, 'get_events_for_date')

        if has_get_events_for_date:
            # Date-based lookup (works for ALL leagues, no tournament/season ID needed)
            for target_date in unique_dates:
                if not pending:
                    break

                finished_events = stats_provider.get_events_for_date(target_date)
                if not finished_events:
                    continue

                for event in finished_events:
                    if not pending:
                        break

                    event_id = event["id"]
                    sc_home = event.get("home_team", "")
                    sc_away = event.get("away_team", "")

                    matched_id: int | None = None
                    for mid in matches_by_date[target_date]:
                        if mid not in pending:
                            continue
                        db_home, db_away = pending[mid]
                        if _match_team_name(sc_home, db_home) and _match_team_name(sc_away, db_away):
                            matched_id = mid
                            break

                    if matched_id is None:
                        continue

                    try:
                        stats_list = stats_provider.get_match_stats(event_id)
                    except Exception:
                        logger.warning("Stats fetch failed for event %s, skipping", event_id)
                        pending.pop(matched_id, None)
                        continue
                    if not stats_list:
                        pending.pop(matched_id, None)
                        continue

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

                    # Commit every 50 matches so progress isn't lost
                    if ingested % 50 < 3:
                        db.commit()
        else:
            # Fallback: tournament-based pagination (legacy)
            max_pages = int(os.getenv("STATS_MAX_PAGES", "40"))
            page = 0
            while page < max_pages and pending:
                finished_events = stats_provider.get_finished_events_page(page=page)
                if not finished_events:
                    break
                for event in finished_events:
                    if not pending:
                        break
                    event_id = event["id"]
                    sc_home = event.get("home_team", "")
                    sc_away = event.get("away_team", "")
                    matched_id = None
                    for mid, (db_home, db_away) in pending.items():
                        if _match_team_name(sc_home, db_home) and _match_team_name(sc_away, db_away):
                            matched_id = mid
                            break
                    if matched_id is None:
                        continue
                    stats_list = stats_provider.get_match_stats(event_id)
                    if not stats_list:
                        pending.pop(matched_id, None)
                        continue
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
                if len(finished_events) < 20:
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
    """Pre-calcula predicciones para TODOS los partidos próximos de todas las ligas."""
    db = SessionLocal()
    try:
        stmt = (
            select(Match)
            .where(Match.status == "SCHEDULED")
            .where(Match.utc_date >= datetime.now(timezone.utc))
            .order_by(Match.utc_date.asc())
        )
        upcoming = list(db.scalars(stmt).all())
        logger.info("Partidos próximos (todas las ligas): %d", len(upcoming))

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
                        result.home_team,
                        result.away_team,
                        result.p_home * 100,
                        result.p_draw * 100,
                        result.p_away * 100,
                    )
            except Exception as exc:
                logger.warning("Error predicción match %d: %s", match.id, exc)

        db.commit()
        logger.info("Predicciones generadas: %d / %d", ok, len(upcoming))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── backfill stats ────────────────────────────────────────────────────────

def backfill_stats() -> None:
    """Backfill stats for finished matches that lack them.

    Processes matches in chronological batches (oldest first),
    respecting rate limits. Designed to run periodically to increase
    stats coverage toward 50%+.
    """
    stats_provider = _get_stats_provider()
    batch_size = int(os.getenv("BACKFILL_BATCH_SIZE", "30"))

    db = SessionLocal()
    try:
        stats_svc = MatchStatsIngestService(db)

        # Find ALL finished matches
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .order_by(Match.utc_date.asc())
        )
        finished = list(db.scalars(stmt).all())

        # Batch check which already have stats
        finished_ids = [m.id for m in finished]
        has_stats: set[int] = set()
        for start in range(0, len(finished_ids), 500):
            batch = finished_ids[start : start + 500]
            rows = db.execute(
                select(MatchStats.match_id)
                .where(MatchStats.match_id.in_(batch))
                .distinct()
            )
            has_stats.update(row.match_id for row in rows)

        needing = [m for m in finished if m.id not in has_stats]
        total_finished = len(finished)
        total_needing = len(needing)
        coverage_before = round((total_finished - total_needing) / max(total_finished, 1) * 100, 1)

        logger.info(
            "Backfill: %d/%d matches need stats (coverage: %.1f%%)",
            total_needing, total_finished, coverage_before,
        )

        if not needing:
            return

        # Process in batches, oldest first
        batch = needing[:batch_size]
        ingested = 0

        for m in batch:
            home_name = m.home_team.name if m.home_team else "Unknown"
            away_name = m.away_team.name if m.away_team else "Unknown"

            # Try to get stats by iterating provider events (match by name)
            events_page = 0
            found = False
            while events_page < 5 and not found:
                events = stats_provider.get_finished_events_page(page=events_page)
                if not events:
                    break
                for event in events:
                    if (_match_team_name(event.get("home_team", ""), home_name)
                            and _match_team_name(event.get("away_team", ""), away_name)):
                        stats_list = stats_provider.get_match_stats(event["id"])
                        if stats_list:
                            ids = stats_svc.ingest_match_stats(
                                stats_list,
                                source_match_id_to_db_id={event["id"]: m.id},
                            )
                            ingested += len(ids)
                            logger.info(
                                "Backfill: match %d (%s vs %s) → %d stats",
                                m.id, home_name, away_name, len(ids),
                            )
                        found = True
                        break
                events_page += 1

        db.commit()

        # Count unique matches that now have stats for accurate coverage
        rows_after = db.execute(
            select(MatchStats.match_id)
            .where(MatchStats.match_id.in_(finished_ids))
            .distinct()
        )
        matches_with_stats = len({row.match_id for row in rows_after})
        coverage_after = round(
            matches_with_stats / max(total_finished, 1) * 100, 1,
        )
        logger.info(
            "Backfill complete: %d stats ingested, coverage: %.1f%% → %.1f%%",
            ingested, coverage_before, coverage_after,
        )
    except Exception:
        db.rollback()
        raise
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
    run_backfill = "--backfill-stats" in args

    # No flags → run sync + predict (original behaviour)
    if not any([run_sync, run_predict, run_sync_players, run_sync_stats, run_backfill]):
        run_sync = True
        run_predict = True

    if run_sync:
        sync_fixtures()
    if run_sync_players:
        sync_players()
    if run_sync_stats:
        sync_match_stats()
    if run_backfill:
        backfill_stats()
    if run_predict:
        precompute_predictions()


if __name__ == "__main__":
    main()
