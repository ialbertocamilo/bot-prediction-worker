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
from dataclasses import dataclass
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


@dataclass
class _LeagueGroup:
    key: str
    display_name: str
    country: str | None
    db_league_ids: list[int]
    provider_name: str | None = None
    provider_league_id: int | None = None
    provider_season: int | None = None


LEAGUE_GROUPS: list[_LeagueGroup] = [
    _LeagueGroup(
        key="liga1-peru",
        display_name="Liga 1 Peru",
        country="Peru",
        db_league_ids=[1, 2],
        provider_name="espn-scraper",
        provider_league_id=670,
        provider_season=2026,
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
        self._id_to_key: dict[int, str] = {}
        for g in LEAGUE_GROUPS:
            for lid in g.db_league_ids:
                self._id_to_key[lid] = g.key

    # ── listar ligas deduplicadas ─────────────────────────────────────────

    def list_leagues(self) -> list[CanonicalLeagueInfo]:
        all_db = list(self.db.scalars(select(League).order_by(League.name)).all())

        result: list[CanonicalLeagueInfo] = []
        seen_keys: set[str] = set()

        for lg in all_db:
            key = self._id_to_key.get(lg.id)
            if key:
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                g = self._groups[key]
                fin, sch = self._count(g.db_league_ids)
                result.append(CanonicalLeagueInfo(
                    index=0, key=key,
                    display_name=g.display_name, country=g.country,
                    db_league_ids=g.db_league_ids,
                    finished_matches=fin, scheduled_matches=sch,
                ))
            else:
                fin, sch = self._count([lg.id])
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
        if not league_ids:
            return 0

        leagues = self.list_leagues()
        info = leagues[canonical_index - 1]
        cfg = self._groups.get(info.key)
        if not cfg or not cfg.provider_name:
            return 0

        if self.get_upcoming(canonical_index):
            return 0

        logger.info(
            "Auto-ingest: '%s' sin partidos, sincronizando desde %s",
            cfg.display_name, cfg.provider_name,
        )

        from app.providers.factory import ProviderFactory
        from app.services.ingest.match_ingest_service import MatchIngestService

        provider = ProviderFactory.create(cfg.provider_name)
        d_from = date.today() - timedelta(days=60)
        d_to = date.today() + timedelta(days=14)
        season = cfg.provider_season or int(os.getenv("DEFAULT_SEASON", "2026"))
        ext_id = cfg.provider_league_id or int(os.getenv("DEFAULT_LEAGUE_ID", "670"))

        results = provider.get_results(
            league_id=ext_id, season=season, date_from=d_from, date_to=d_to,
        )
        fixtures = provider.get_fixtures(
            league_id=ext_id, season=season, date_from=d_from, date_to=d_to,
        )
        all_m = results + fixtures

        if not all_m:
            return 0

        svc = MatchIngestService(self.db)
        ids = svc.ingest_matches(all_m)
        self.db.commit()
        logger.info("Auto-ingest: %d partidos para '%s'", len(ids), cfg.display_name)
        return len(ids)

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

    def _dedup(self, matches: list[Match]) -> list[Match]:
        """Elimina duplicados (mismos equipos, mismo día).

        Prefiere el partido de la liga con más datos (mejor para predicción).
        """
        count_cache: dict[int, int] = {}
        for m in matches:
            if m.league_id not in count_cache:
                count_cache[m.league_id] = self.db.scalar(
                    select(func.count(Match.id))
                    .where(Match.league_id == m.league_id)
                    .where(Match.status == "FINISHED")
                ) or 0

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
