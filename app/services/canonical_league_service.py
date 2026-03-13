"""
Canonical League Service — deduplicación de ligas entre proveedores.

Cuando distintos proveedores crean ligas separadas en la DB para la misma
competición real (ej. "Primera División" de ESPN y "Peruvian Liga 1" de
SofaScore), este servicio las agrupa en una sola liga canónica.

Configuración: editar LEAGUE_GROUPS abajo.  Ligas que NO aparecen en
ningún grupo se muestran tal cual (standalone).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.repositories.football.match_repository import MatchRepository

logger = logging.getLogger(__name__)


# ── Configuración de grupos canónicos ─────────────────────────────────────
# Cada grupo une varios league_ids de la DB que apuntan a la MISMA liga.
# provider_*: se usa para auto-ingest cuando no hay partidos programados.
# league_names: nombres que ESPN (u otro provider) puede dar a esta liga
#               en la DB.  Se usan para auto-descubrir db_league_ids.
# provider_slug: provider-specific identifier for the league (e.g. "per.1" for ESPN).


@dataclass
class _LeagueGroup:
    key: str
    display_name: str
    country: str | None
    db_league_ids: list[int]
    league_names: list[str] = field(default_factory=list)
    provider_slug: str | None = None        # provider-specific league identifier
    provider_name: str | None = None
    provider_league_id: int | None = None
    provider_season: int | None = None


LEAGUE_GROUPS: list[_LeagueGroup] = [
    _LeagueGroup(
        key="liga1-peru",
        display_name="Liga 1 Peru",
        country="Peru",
        db_league_ids=[1, 2],
        league_names=["Primera División", "Peruvian Liga 1", "Liga 1"],
        provider_slug="per.1",
        provider_name="espn-scraper",
        provider_league_id=670,
        provider_season=2026,
    ),
    _LeagueGroup(
        key="champions-league",
        display_name="Champions League",
        country=None,
        db_league_ids=[],
        league_names=["UEFA Champions League", "Champions League"],
        provider_slug="uefa.champions",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="premier-league",
        display_name="Premier League",
        country="England",
        db_league_ids=[],
        league_names=["English Premier League", "Premier League"],
        provider_slug="eng.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="la-liga",
        display_name="La Liga",
        country="Spain",
        db_league_ids=[],
        league_names=["Spanish LaLiga", "La Liga", "LaLiga"],
        provider_slug="esp.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="bundesliga",
        display_name="Bundesliga",
        country="Germany",
        db_league_ids=[],
        league_names=["German Bundesliga", "Bundesliga"],
        provider_slug="ger.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="serie-a",
        display_name="Serie A",
        country="Italy",
        db_league_ids=[],
        league_names=["Italian Serie A", "Serie A"],
        provider_slug="ita.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="ligue-1",
        display_name="Ligue 1",
        country="France",
        db_league_ids=[],
        league_names=["French Ligue 1", "Ligue 1"],
        provider_slug="fra.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="mls",
        display_name="MLS",
        country="USA",
        db_league_ids=[],
        league_names=["Major League Soccer", "MLS"],
        provider_slug="usa.1",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="europa-league",
        display_name="Europa League",
        country=None,
        db_league_ids=[],
        league_names=["UEFA Europa League", "Europa League"],
        provider_slug="uefa.europa",
        provider_name="espn-scraper",
    ),
    _LeagueGroup(
        key="copa-libertadores",
        display_name="Copa Libertadores",
        country=None,
        db_league_ids=[],
        league_names=["Copa Libertadores", "CONMEBOL Libertadores", "Libertadores"],
        provider_slug="conmebol.libertadores",
        provider_name="espn-scraper",
    ),
]


# ── Tipo público para consumidores ────────────────────────────────────────


@dataclass
class CanonicalLeagueInfo:
    """Liga canónica deduplicada para mostrar en bot / API."""
    index: int                    # 1-based, número visible al usuario
    key: str
    display_name: str
    country: str | None
    db_league_ids: list[int]
    finished_matches: int = 0
    scheduled_matches: int = 0


# ── Service ───────────────────────────────────────────────────────────────


class CanonicalLeagueService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._groups = {g.key: g for g in LEAGUE_GROUPS}
        # Build resolved ID mappings: group key → resolved DB IDs
        self._resolved: dict[str, list[int]] = {}
        self._id_to_key: dict[int, str] = {}
        for g in LEAGUE_GROUPS:
            ids = self._resolve_league_ids(g)
            self._resolved[g.key] = ids
            for lid in ids:
                self._id_to_key[lid] = g.key

    # ── resolver IDs de DB dinámicamente ─────────────────────────────────

    def _resolve_league_ids(self, group: _LeagueGroup) -> list[int]:
        """Combine static db_league_ids + auto-discovery by league_names."""
        ids: set[int] = set(group.db_league_ids)
        if group.league_names:
            all_leagues = list(self.db.scalars(select(League)))
            for lg in all_leagues:
                if self._name_matches(lg.name, group.league_names):
                    ids.add(lg.id)
        return sorted(ids)

    @staticmethod
    def _name_matches(db_name: str, patterns: list[str]) -> bool:
        """Case-insensitive match: exact OR either is substring of the other."""
        db_lower = db_name.lower().strip()
        for p in patterns:
            p_lower = p.lower().strip()
            if db_lower == p_lower or p_lower in db_lower or db_lower in p_lower:
                return True
        return False

    # ── listar ligas deduplicadas ─────────────────────────────────────────

    def list_leagues(self) -> list[CanonicalLeagueInfo]:
        result: list[CanonicalLeagueInfo] = []
        grouped_ids: set[int] = set()

        # Collect all league IDs that belong to configured groups
        all_group_ids: list[int] = []
        for g in LEAGUE_GROUPS:
            ids = self._resolved.get(g.key, [])
            grouped_ids.update(ids)
            all_group_ids.extend(ids)

        # Batch counts: single query for all league_ids
        count_map = self._batch_counts(all_group_ids)

        # 1. All configured groups (even if no DB data yet)
        for g in LEAGUE_GROUPS:
            ids = self._resolved.get(g.key, [])
            fin = sum(count_map.get(lid, (0, 0))[0] for lid in ids)
            sch = sum(count_map.get(lid, (0, 0))[1] for lid in ids)
            result.append(CanonicalLeagueInfo(
                index=0, key=g.key,
                display_name=g.display_name, country=g.country,
                db_league_ids=ids,
                finished_matches=fin, scheduled_matches=sch,
            ))

        # 2. Standalone DB leagues not in any group
        all_db = list(self.db.scalars(select(League).order_by(League.name)).all())
        standalone_ids = [lg.id for lg in all_db if lg.id not in grouped_ids]
        if standalone_ids:
            standalone_counts = self._batch_counts(standalone_ids)
            for lg in all_db:
                if lg.id not in grouped_ids:
                    fin, sch = standalone_counts.get(lg.id, (0, 0))
                    result.append(CanonicalLeagueInfo(
                        index=0, key=f"league-{lg.id}",
                        display_name=lg.name, country=lg.country,
                        db_league_ids=[lg.id],
                        finished_matches=fin, scheduled_matches=sch,
                    ))

        for i, item in enumerate(result, 1):
            item.index = i
        return result

    # ── partidos próximos (deduplicados) ──────────────────────────────────

    def get_upcoming(
        self,
        canonical_index: int | None = None,
        days_ahead: int = 14,
    ) -> list[Match]:
        """Devuelve partidos SCHEDULED deduplicados.

        canonical_index: 1-based (de list_leagues). None → todas las ligas.
        """
        now = datetime.now(timezone.utc)
        date_to = now + timedelta(days=days_ahead)
        repo = MatchRepository(self.db)

        if canonical_index is not None:
            league_ids = self._ids_for_index(canonical_index)
            if not league_ids:
                return []
            all_m: list[Match] = []
            for lid in league_ids:
                all_m.extend(
                    repo.list_by_date_range(date_from=now, date_to=date_to, league_id=lid)
                )
        else:
            all_m = repo.list_by_date_range(date_from=now, date_to=date_to)

        upcoming = [m for m in all_m if m.status in ("SCHEDULED", "NS")]
        upcoming = self._dedup(upcoming)
        upcoming.sort(key=lambda m: m.utc_date or datetime.min.replace(tzinfo=timezone.utc))
        return upcoming

    # ── nombre canónico para un league_id de la DB ────────────────────────

    def display_name_for(self, league_id: int) -> str:
        key = self._id_to_key.get(league_id)
        if key:
            return self._groups[key].display_name
        lg = self.db.get(League, league_id)
        return lg.name if lg else "?"

    # ── auto-ingest si la liga canónica no tiene partidos ─────────────────

    def auto_ingest_if_empty(self, canonical_index: int) -> int:
        """Sincroniza desde el provider configurado si no hay programados.

        Retorna cantidad de partidos ingestados (0 si no fue necesario).
        """
        league_ids = self._ids_for_index(canonical_index)

        leagues = self.list_leagues()
        if canonical_index < 1 or canonical_index > len(leagues):
            return 0
        info = leagues[canonical_index - 1]
        cfg = self._groups.get(info.key)
        if not cfg or not cfg.provider_name:
            return 0

        if league_ids and self.get_upcoming(canonical_index):
            return 0

        return self._ingest_from_provider(cfg)

    def ingest_league(self, key: str, days_back: int = 180, days_ahead: int = 30) -> int:
        """Ingest results + fixtures for a league group by key.

        Returns number of matches ingested.
        """
        cfg = self._groups.get(key)
        if not cfg or not cfg.provider_name:
            logger.warning("ingest_league: no provider configured for %s", key)
            return 0
        return self._ingest_from_provider(cfg, days_back=days_back, days_ahead=days_ahead)

    def seed_all_leagues(self, days_back: int = 180, days_ahead: int = 30) -> int:
        """Ingest all configured leagues. Returns total matches ingested."""
        total = 0
        for i, g in enumerate(LEAGUE_GROUPS, 1):
            if not g.provider_name:
                continue
            logger.info(
                "=== [%d/%d] Seeding %s ===", i, len(LEAGUE_GROUPS), g.display_name,
            )
            n = self._ingest_from_provider(g, days_back=days_back, days_ahead=days_ahead)
            total += n
            logger.info("%s: %d partidos ingestados", g.display_name, n)
        # Rebuild resolved IDs after all ingests
        self._rebuild_mappings()
        return total

    def _ingest_from_provider(
        self,
        cfg: _LeagueGroup,
        days_back: int = 60,
        days_ahead: int = 14,
    ) -> int:
        """Core ingest logic shared by auto_ingest and seed."""
        logger.info(
            "Ingest: '%s' desde %s (slug=%s)",
            cfg.display_name, cfg.provider_name, cfg.provider_slug,
        )

        provider = self._create_provider(cfg)

        d_from = date.today() - timedelta(days=days_back)
        d_to = date.today() + timedelta(days=days_ahead)
        season = cfg.provider_season or int(os.getenv("DEFAULT_SEASON", "2026"))
        ext_id = cfg.provider_league_id or 0

        results = provider.get_results(
            league_id=ext_id, season=season, date_from=d_from, date_to=d_to,
        )
        fixtures = provider.get_fixtures(
            league_id=ext_id, season=season, date_from=d_from, date_to=d_to,
        )
        all_m = results + fixtures

        if not all_m:
            logger.info("Ingest: 0 partidos obtenidos para '%s'", cfg.display_name)
            return 0

        from app.services.ingest.match_ingest_service import MatchIngestService

        svc = MatchIngestService(self.db)
        ids = svc.ingest_matches(all_m)
        self.db.commit()

        # Rebuild resolved IDs so new leagues are visible immediately
        self._rebuild_mappings()

        # Post-ingest: propagate country & set is_current on seasons
        self._fix_league_metadata(cfg)

        logger.info(
            "Ingest: %d partidos para '%s' (provider=%s)",
            len(ids), cfg.display_name, cfg.provider_name,
        )
        return len(ids)

    def _create_provider(self, cfg: _LeagueGroup):
        """Create provider instance via ProviderFactory.

        Uses provider_slug to configure provider-specific league routing.
        All provider-specific logic stays inside the providers layer.
        """
        from app.providers.factory import ProviderFactory

        return ProviderFactory.create(cfg.provider_name, league_slug=cfg.provider_slug)

    def _rebuild_mappings(self) -> None:
        """Re-resolve DB IDs after ingest creates new league entries."""
        self._resolved.clear()
        self._id_to_key.clear()
        for g in LEAGUE_GROUPS:
            ids = self._resolve_league_ids(g)
            self._resolved[g.key] = ids
            for lid in ids:
                self._id_to_key[lid] = g.key

    def _fix_league_metadata(self, cfg: _LeagueGroup) -> None:
        """Propagate country from LeagueGroup config to DB leagues,
        and mark the current season's is_current flag."""
        from app.db.models.football.season import Season

        ids = self._resolved.get(cfg.key, [])
        if not ids:
            return

        # Update league country if NULL
        if cfg.country:
            for lid in ids:
                lg = self.db.get(League, lid)
                if lg and lg.country is None:
                    # Check if another league already owns (name, country)
                    conflict = self.db.scalar(
                        select(League.id)
                        .where(League.name == lg.name, League.country == cfg.country)
                        .where(League.id != lg.id)
                    )
                    if conflict:
                        logger.warning(
                            "Skip country update for league %d ('%s'): "
                            "duplicate (name=%s, country=%s) on league %d",
                            lid, lg.name, lg.name, cfg.country, conflict,
                        )
                        continue
                    lg.country = cfg.country

        # Mark latest season as is_current
        current_year = date.today().year
        for lid in ids:
            stmt = (
                select(Season)
                .where(Season.league_id == lid)
                .order_by(Season.year.desc())
            )
            seasons = list(self.db.scalars(stmt).all())
            for s in seasons:
                should_be_current = s.year >= current_year
                if s.is_current != should_be_current:
                    s.is_current = should_be_current

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "_fix_league_metadata: commit failed for '%s'", cfg.display_name,
            )

    # ── helpers privados ──────────────────────────────────────────────────

    def _ids_for_index(self, canonical_index: int) -> list[int]:
        leagues = self.list_leagues()
        if canonical_index < 1 or canonical_index > len(leagues):
            return []
        return leagues[canonical_index - 1].db_league_ids

    def _count(self, league_ids: list[int]) -> tuple[int, int]:
        fin = self.db.scalar(
            select(func.count(Match.id))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
        ) or 0
        sch = self.db.scalar(
            select(func.count(Match.id))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status.in_(("SCHEDULED", "NS")))
        ) or 0
        return fin, sch

    def _batch_counts(self, league_ids: list[int]) -> dict[int, tuple[int, int]]:
        """Fetch finished & scheduled counts for all league_ids in 2 queries.

        Returns ``{league_id: (finished, scheduled)}``.
        """
        if not league_ids:
            return {}

        result: dict[int, tuple[int, int]] = {lid: (0, 0) for lid in league_ids}

        # Finished counts grouped by league_id
        fin_stmt = (
            select(Match.league_id, func.count(Match.id))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status == "FINISHED")
            .group_by(Match.league_id)
        )
        for row in self.db.execute(fin_stmt):
            old = result.get(row[0], (0, 0))
            result[row[0]] = (row[1], old[1])

        # Scheduled counts grouped by league_id
        sch_stmt = (
            select(Match.league_id, func.count(Match.id))
            .where(Match.league_id.in_(league_ids))
            .where(Match.status.in_(("SCHEDULED", "NS")))
            .group_by(Match.league_id)
        )
        for row in self.db.execute(sch_stmt):
            old = result.get(row[0], (0, 0))
            result[row[0]] = (old[0], row[1])

        return result

    def _dedup(self, matches: list[Match]) -> list[Match]:
        """Elimina duplicados (mismos equipos, mismo día).

        Prefiere el partido de la liga con más datos (mejor para predicción).
        Uses a single batch query instead of per-league COUNT.
        """
        if not matches:
            return []

        league_ids = list({m.league_id for m in matches})
        counts = self._batch_counts(league_ids)
        count_cache = {lid: c[0] for lid, c in counts.items()}

        # Ordenar: liga con más datos primero
        by_data = sorted(
            matches,
            key=lambda m: count_cache.get(m.league_id, 0),
            reverse=True,
        )

        seen: set[tuple] = set()
        result: list[Match] = []
        for m in by_data:
            d = m.utc_date.date() if m.utc_date else None
            sig = (m.home_team_id, m.away_team_id, d)
            if sig not in seen:
                seen.add(sig)
                result.append(m)
        return result
