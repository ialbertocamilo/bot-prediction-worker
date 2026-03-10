from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalPlayer,
    CanonicalTeam,
)
from app.providers.base import BaseProvider
from app.providers.transfermarkt.client import TransfermarktClient
from app.providers.transfermarkt.mapper import TransfermarktMapper

logger = logging.getLogger(__name__)


class TransfermarktProvider(BaseProvider):
    """Provider de datos de jugadores vía scraping de Transfermarkt.

    Especializado en:
      - Plantillas de equipos (get_players)
      - Datos individuales de jugadores (nombre, posición, valor de mercado, etc.)

    Los métodos de partidos delegan al proveedor principal (no soportados aquí).
    """

    def __init__(self, client: TransfermarktClient | None = None) -> None:
        self.client: TransfermarktClient = client or TransfermarktClient()

    # ── Método principal: jugadores ─────────────────────────────

    def get_players(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalPlayer]:
        """Scrapeamos la lista de equipos de la liga y luego cada plantilla."""
        teams_soup = self.client.get_teams_page(season)
        team_links = TransfermarktClient.parse_team_links(teams_soup)

        if not team_links:
            logger.warning("Transfermarkt: no se encontraron equipos para season %d", season)
            return []

        logger.info(
            "Transfermarkt: %d equipos encontrados, scrapeando plantillas…",
            len(team_links),
        )

        all_players: list[CanonicalPlayer] = []

        for team_info in team_links:
            team_name: str = team_info["name"]
            squad_path: str = team_info["squad_path"]

            try:
                squad_soup = self.client.get_squad_page(squad_path, season)
                raw_players: list[dict[str, Any]] = TransfermarktClient.parse_squad_table(squad_soup)

                for raw in raw_players:
                    player = TransfermarktMapper.map_player(raw, team_name=team_name)
                    if player is not None:
                        all_players.append(player)

                logger.info(
                    "Transfermarkt: %s → %d jugadores",
                    team_name,
                    len(raw_players),
                )
            except Exception:
                logger.exception("Transfermarkt: error scrapeando %s", team_name)

        logger.info("Transfermarkt: total %d jugadores obtenidos", len(all_players))
        return all_players

    # ── Métodos de partidos — no soportados por Transfermarkt ───

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
        """Extrae equipos de la página de la liga."""
        soup = self.client.get_teams_page(season)
        team_links = TransfermarktClient.parse_team_links(soup)
        return [
            CanonicalTeam(name=t["name"], short_name=None, country=None, founded_year=None)
            for t in team_links
        ]

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        soup = self.client.get_teams_page(season)
        header = soup.select_one("h1.data-header__headline-wrapper")
        if header:
            return CanonicalLeague(name=header.get_text(strip=True))
        return None
