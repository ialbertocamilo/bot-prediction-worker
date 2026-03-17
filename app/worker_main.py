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
import re
import sys
import threading
import unicodedata
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

# ── Team name matching (ESPN ↔ SofaScore) ────────────────────────────────

# Noise words stripped before comparison — differ between providers
_TEAM_NOISE = frozenset({
    "fc", "cf", "sc", "cd", "ac", "as", "us", "ss", "rcd", "afc", "bsc",
    "fk", "sk", "nk", "pk", "sv", "tsv", "vfl", "vfb",
    "de", "del", "fsv", "tsg", "1.", "1899", "1848", "1860",
    "04", "05", "09",
})

# ── Entity-resolution alias dictionary ───────────────────────────────────
# Maps normalized team names → canonical form so different sources
# (ESPN, SofaScore, etc.) resolve to the same entity.
# Keys MUST be the output of _normalize_team().
TEAM_NAME_ALIASES: dict[str, str] = {
    # ── Italy / Serie A ──
    "internazionale": "inter milan",
    "inter": "inter milan",
    "fc internazionale milano": "inter milan",
    "fc inter milano": "inter milan",
    # ── MLS (prevent Inter Miami ↔ Inter Milan cross-match) ──
    "inter miami cf": "inter miami",
    "inter miami": "inter miami",
    "cf montreal": "cf montreal",
    "lafc": "los angeles fc",
    "la fc": "los angeles fc",
    "los angeles fc": "los angeles fc",
    "houston dynamo fc": "houston dynamo",
    "houston dynamo": "houston dynamo",
    "new england revolution": "new england revolution",
    # ── Germany / Bundesliga ──
    "fc cologne": "koln",
    "cologne": "koln",
    "1 fc koln": "koln",
    "fc koln": "koln",
    "fc augsburg": "augsburg",
    # ── Belgium / Europa League ──
    "racing genk": "genk",
    "krc genk": "genk",
    # ── Serbia / Champions / Europa ──
    "red star belgrade": "crvena zvezda",
    "fk crvena zvezda": "crvena zvezda",
    "crvena zvezda": "crvena zvezda",
    # ── France ──
    "paris saint germain": "psg",
    "paris saint-germain": "psg",
    "paris sg": "psg",
    "psg": "psg",
    # ── Norway / Champions ──
    "bodo/glimt": "bodo glimt",
    "bodo glimt": "bodo glimt",
    "fk bodo glimt": "bodo glimt",
    "fk bodoglimt": "bodo glimt",
    # ── Croatia / Europa ──
    "dinamo zagreb": "dinamo zagreb",
    "gnk dinamo zagreb": "dinamo zagreb",
    # ── Argentina / Primera División ──
    "racing club": "racing club arg",
    "racing": "racing club arg",
    "club atletico independiente": "independiente arg",
    "independiente": "independiente arg",
    "ca independiente": "independiente arg",
    # ── Ecuador (prevent Independiente del Valle ↔ Independiente ARG) ──
    "independiente del valle": "independiente del valle",
    # ── Peru ──
    "adt": "adt de tarma",
    "asociacion deportiva tarma": "adt de tarma",
    "utc": "utc cajamarca",
    "universidad tecnica de cajamarca": "utc cajamarca",
    "juan pablo ii": "juan pablo ii",
    # ── Libertadores / South America ──
    "atletico mineiro": "atletico mineiro",
    "atletico mg": "atletico mineiro",
    "atletico madrid": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "club atletico de madrid": "atletico madrid",
}


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    base = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Characters that NFKD doesn't decompose into base + combining
    return base.translate(str.maketrans({"ø": "o", "Ø": "O", "ð": "d", "ł": "l", "æ": "ae", "ß": "ss"}))


def _normalize_team(name: str) -> str:
    name = _strip_accents(name.lower().strip())
    name = re.sub(r"[.\-'\"()/]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _resolve_alias(name: str) -> str:
    """Resolve a team name via the alias dictionary.

    Returns the canonical form if found, otherwise the normalized name.
    """
    normalized = _normalize_team(name)
    return TEAM_NAME_ALIASES.get(normalized, normalized)


def _core_tokens(name: str) -> set[str]:
    tokens = set(_normalize_team(name).split())
    meaningful = {t for t in tokens if t not in _TEAM_NOISE and not t.isdigit()}
    return meaningful if meaningful else tokens


def _team_name_score(name_a: str, name_b: str) -> float:
    """Return a similarity score [0.0, 1.0] between two team names.

    Resolves aliases first, then uses layered heuristics:
    alias → exact → word-boundary → token overlap → fuzzy.
    Does NOT make any match/no-match decision — callers decide the threshold.
    """
    # Resolve through alias dictionary before any comparison
    na = _resolve_alias(name_a)
    nb = _resolve_alias(name_b)

    if na == nb:
        return 1.0

    # Word-boundary containment
    if len(na) >= 4 and len(nb) >= 4:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if re.search(r"\b" + re.escape(shorter) + r"\b", longer):
            return 0.90

    # Token-level fuzzy match (from resolved names)
    ta = _core_tokens(na)
    tb = _core_tokens(nb)
    if ta and tb:
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        matched = 0
        for s in smaller:
            for l in larger:
                if s == l or (
                    len(s) >= 4
                    and len(l) >= 4
                    and SequenceMatcher(None, s, l).ratio() > 0.65
                ):
                    matched += 1
                    break
        ratio = matched / len(smaller)
        if ratio > 0:
            return 0.5 + 0.4 * ratio  # maps (0,1] → (0.5, 0.9]

    # Raw sequence similarity on normalized name (last resort)
    return SequenceMatcher(None, na, nb).ratio()


# Minimum name score to even consider a pair (low on purpose — score
# verification does the real filtering).
_NAME_THRESHOLD = 0.45


def _find_best_match(
    db_match: Match,
    db_home: str,
    db_away: str,
    events: list[dict],
    used_event_ids: set[str],
) -> dict | None:
    """Find the SofaScore event that best matches a DB match.

    Strategy (in order):
      1. Name similarity must be above _NAME_THRESHOLD for BOTH teams.
      2. Among candidates, prefer those where the **score matches exactly**.
      3. If multiple score-verified candidates, pick highest name score.
      4. Never return an event already assigned to another DB match.
      5. Minimum per-team name score: 0.75 (score-verified), 0.45 (candidate).
    """
    db_hg = db_match.home_goals
    db_ag = db_match.away_goals

    best: dict | None = None
    best_score: float = 0.0
    best_score_verified: bool = False
    best_h: float = 0.0
    best_a: float = 0.0

    for ev in events:
        eid = ev["id"]
        if eid in used_event_ids:
            continue

        h_score = _team_name_score(ev.get("home_team", ""), db_home)
        a_score = _team_name_score(ev.get("away_team", ""), db_away)

        if h_score < _NAME_THRESHOLD or a_score < _NAME_THRESHOLD:
            continue

        combined = (h_score + a_score) / 2

        # Score verification: goals from SofaScore must match DB
        score_ok = (
            db_hg is not None
            and ev.get("home_goals") is not None
            and db_hg == ev["home_goals"]
            and db_ag == ev.get("away_goals")
        )

        # Score-verified candidate always beats non-verified
        if score_ok and not best_score_verified:
            best, best_score, best_score_verified = ev, combined, True
            best_h, best_a = h_score, a_score
        elif score_ok and best_score_verified and combined > best_score:
            best, best_score = ev, combined
            best_h, best_a = h_score, a_score
        elif not score_ok and not best_score_verified and combined > best_score:
            best, best_score = ev, combined
            best_h, best_a = h_score, a_score

    if best is None:
        return None

    # Non-verified: combined name must be very high
    if not best_score_verified and best_score < 0.85:
        return None

    # Score-verified: each team name must still be reasonably close.
    # This blocks e.g. "Manchester City" (0.70) matching "Manchester United"
    # even when the score happens to coincide.
    if best_score_verified and min(best_h, best_a) < 0.75:
        return None

    return best


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
        match_lookup: dict[int, Match] = {}
        for m in needing_stats:
            if m.utc_date:
                d = m.utc_date.date() if hasattr(m.utc_date, 'date') else m.utc_date
                matches_by_date[d].append(m.id)
                match_dates[m.id] = d
                match_lookup[m.id] = m

        unique_dates = sorted(matches_by_date.keys(), reverse=True)
        logger.info("Stats sync: %d unique dates to query", len(unique_dates))

        ingested = 0
        has_get_events_for_date = hasattr(stats_provider, 'get_events_for_date')

        if has_get_events_for_date:
            # Date-based lookup (works for ALL leagues, no tournament/season ID needed)
            skipped_dates = 0
            for target_date in unique_dates:
                if not pending:
                    break

                try:
                    finished_events = stats_provider.get_events_for_date(target_date)
                except Exception:
                    logger.warning("Stats date %s failed (503/timeout), skipping", target_date)
                    skipped_dates += 1
                    continue
                if not finished_events:
                    continue

                # For each DB match on this date, find best SofaScore event
                used_event_ids: set[str] = set()
                for mid in matches_by_date[target_date]:
                    if mid not in pending:
                        continue
                    db_home, db_away = pending[mid]
                    db_m = match_lookup[mid]

                    event = _find_best_match(
                        db_m, db_home, db_away,
                        finished_events, used_event_ids,
                    )
                    if event is None:
                        continue

                    event_id = event["id"]
                    used_event_ids.add(event_id)

                    try:
                        stats_list = stats_provider.get_match_stats(event_id)
                    except Exception:
                        logger.warning("Stats fetch failed for event %s, skipping", event_id)
                        pending.pop(mid, None)
                        continue
                    if not stats_list:
                        pending.pop(mid, None)
                        continue

                    ids = stats_svc.ingest_match_stats(
                        stats_list,
                        source_match_id_to_db_id={event_id: mid},
                    )
                    ingested += len(ids)
                    pending.pop(mid, None)

                    logger.info(
                        "Stats event %s → match %d (%s vs %s): %d registros",
                        event_id, mid,
                        event.get("home_team", ""), event.get("away_team", ""),
                        len(ids),
                    )

                    # Commit every 50 matches so progress isn't lost
                    if ingested % 50 < 3:
                        db.commit()
        else:
            # Fallback: tournament-based pagination (legacy)
            max_pages = int(os.getenv("STATS_MAX_PAGES", "40"))
            page = 0
            all_legacy_events: list[dict] = []
            while page < max_pages and pending:
                finished_events = stats_provider.get_finished_events_page(page=page)
                if not finished_events:
                    break
                all_legacy_events.extend(finished_events)
                if len(finished_events) < 20:
                    break
                page += 1

            used_event_ids_legacy: set[str] = set()
            for mid in list(pending.keys()):
                db_home, db_away = pending[mid]
                db_m = match_lookup.get(mid)
                if db_m is None:
                    continue
                event = _find_best_match(
                    db_m, db_home, db_away,
                    all_legacy_events, used_event_ids_legacy,
                )
                if event is None:
                    continue
                event_id = event["id"]
                used_event_ids_legacy.add(event_id)
                stats_list = stats_provider.get_match_stats(event_id)
                if not stats_list:
                    pending.pop(mid, None)
                    continue
                ids = stats_svc.ingest_match_stats(
                    stats_list,
                    source_match_id_to_db_id={event_id: mid},
                )
                ingested += len(ids)
                pending.pop(mid, None)
                logger.info(
                    "Stats event %s → match %d (%s vs %s): %d registros",
                    event_id, mid,
                    event.get("home_team", ""), event.get("away_team", ""),
                    len(ids),
                )

        db.commit()
        logger.info("Total stats ingresados: %d registros", ingested)
        if has_get_events_for_date and skipped_dates:
            logger.warning("Fechas omitidas por error (503/timeout): %d — relanzar sync para reintentar", skipped_dates)
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

            # Try to get stats by iterating provider events (match by name + score)
            events_page = 0
            found = False
            all_events: list[dict] = []
            while events_page < 5:
                events = stats_provider.get_finished_events_page(page=events_page)
                if not events:
                    break
                all_events.extend(events)
                events_page += 1

            used: set[str] = set()
            event = _find_best_match(m, home_name, away_name, all_events, used)
            if event is not None:
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
