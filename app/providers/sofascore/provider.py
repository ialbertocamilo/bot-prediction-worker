from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalMatchStats,
    CanonicalPlayer,
    CanonicalTeam,
)
from app.providers.base import BaseProvider
from app.providers.sofascore.client import SofaScoreClient
from app.providers.sofascore.mapper import SofaScoreMapper

logger = logging.getLogger(__name__)


class SofaScoreProvider(BaseProvider):
    """Provider de estadísticas y jugadores vía SofaScore API.

    Especializado en:
      - Estadísticas detalladas de partidos (get_match_stats)
      - Jugadores extraídos desde lineups (get_players)
    """

    def __init__(self, client: SofaScoreClient | None = None) -> None:
        self.client: SofaScoreClient = client or SofaScoreClient()

    # ── Método principal: estadísticas de partidos ──────────────

    def get_match_stats(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchStats]:
        """Obtiene estadísticas de un partido desde SofaScore.

        Primero obtiene el detalle del partido para los nombres de equipos,
        luego las estadísticas y mapea a CanonicalMatchStats.
        """
        # 1) Obtener nombres de equipos
        try:
            detail = self.client.get_match_detail(match_external_id)
        except Exception:
            logger.exception(
                "SofaScore: error obteniendo detalle de event %s",
                match_external_id,
            )
            return []

        event: dict[str, Any] = detail.get("event", detail)
        home_team: dict[str, Any] = event.get("homeTeam", {})
        away_team: dict[str, Any] = event.get("awayTeam", {})
        home_name: str = home_team.get("name", "Unknown")
        away_name: str = away_team.get("name", "Unknown")

        # 2) Obtener estadísticas
        try:
            stats_payload = self.client.get_match_statistics(match_external_id)
        except Exception:
            logger.exception(
                "SofaScore: error obteniendo stats de event %s",
                match_external_id,
            )
            return []

        result = SofaScoreMapper.map_match_stats(
            stats_payload=stats_payload,
            event_id=match_external_id,
            home_team_name=home_name,
            away_team_name=away_name,
        )

        logger.info(
            "SofaScore: event %s → %d registros de stats (%s vs %s)",
            match_external_id,
            len(result),
            home_name,
            away_name,
        )
        return result

    def get_finished_events_page(self, page: int = 0) -> list[dict]:
        """Return a page of finished events from SofaScore tournament."""
        try:
            data = self.client.get_tournament_events(page=page, direction="last")
        except Exception:
            logger.exception("SofaScore: error fetching events page %d", page)
            return []

        events = data.get("events", [])
        result = []
        for event in events:
            if event.get("status", {}).get("type", "") != "finished":
                continue
            result.append({
                "id": str(event["id"]),
                "home_team": event.get("homeTeam", {}).get("name", ""),
                "away_team": event.get("awayTeam", {}).get("name", ""),
            })
        return result

    # ── Métodos de partidos — no soportados como foco ───────────

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        return []

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        return []

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        return []

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        return []

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        return None

    # ── Jugadores desde lineups ─────────────────────────────────

    def get_players(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalPlayer]:
        """Extrae jugadores únicos desde los lineups de partidos terminados.

        Recorre las páginas de eventos del torneo, obtiene lineups de cada
        partido terminado, y acumula jugadores deduplicando por SofaScore
        player ID.
        """
        seen_ids: set[str] = set()
        players: list[CanonicalPlayer] = []
        page = 0
        max_pages = 10  # protección contra loops infinitos

        while page < max_pages:
            try:
                data = self.client.get_tournament_events(page=page, direction="last")
            except Exception:
                logger.exception("SofaScore: error obteniendo eventos página %d", page)
                break

            events: list[dict[str, Any]] = data.get("events", [])
            if not events:
                break

            for event in events:
                status = event.get("status", {}).get("type", "")
                if status != "finished":
                    continue

                event_id = str(event["id"])
                home_name = event.get("homeTeam", {}).get("name", "Unknown")
                away_name = event.get("awayTeam", {}).get("name", "Unknown")

                try:
                    lineups = self.client.get_match_lineups(event_id)
                except Exception:
                    logger.warning(
                        "SofaScore: no se pudo obtener lineups de event %s",
                        event_id,
                    )
                    continue

                batch = SofaScoreMapper.map_players_from_lineups(
                    lineups_payload=lineups,
                    home_team_name=home_name,
                    away_team_name=away_name,
                )

                for p in batch:
                    pid = p.source_ref.external_id if p.source_ref else p.name
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        players.append(p)

            # SofaScore: si la página tiene <30 eventos, es la última
            if len(events) < 30:
                break
            page += 1

        logger.info(
            "SofaScore: extraídos %d jugadores únicos de %d páginas de eventos",
            len(players),
            page + 1,
        )
        return players
