from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalTeam,
)
from app.domain.enums import MatchStatus
from app.providers.base import BaseProvider
from app.providers.espn_scraper.client import EspnScraperClient
from app.providers.espn_scraper.mapper import EspnScraperMapper

logger = logging.getLogger(__name__)


class EspnScraperProvider(BaseProvider):
    """Provider basado en ESPN public API (scraper).

    Ideal para ligas no cubiertas por APIs de pago, como la Liga 1 Perú.
    No necesita API key — los endpoints son públicos.

    Slugs soportados:
        per.1  → Liga 1 Perú
        arg.1  → Liga Profesional Argentina
        bra.1  → Brasileirão Série A
        col.1  → Liga BetPlay Colombia
        mex.1  → Liga MX
        (y muchos más)
    """

    def __init__(self, client: EspnScraperClient | None = None) -> None:
        self.client: EspnScraperClient = client or EspnScraperClient()

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        raw_events = self.client.get_matches_in_range(date_from, date_to)
        matches = []
        for event in raw_events:
            m = EspnScraperMapper.map_match(event)
            if m.status == MatchStatus.scheduled:
                # Inyectar league_name desde el contexto global si falta
                if m.league_name is None:
                    m = m.model_copy(update={"league_name": self._get_league_name()})
                matches.append(m)
        logger.info(
            "ESPN scraper fixtures: %d SCHEDULED (%s → %s)",
            len(matches), date_from, date_to,
        )
        return matches

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        raw_events = self.client.get_matches_in_range(date_from, date_to)
        matches = []
        for event in raw_events:
            m = EspnScraperMapper.map_match(event)
            if m.status == MatchStatus.finished:
                if m.league_name is None:
                    m = m.model_copy(update={"league_name": self._get_league_name()})
                matches.append(m)
        logger.info(
            "ESPN scraper results: %d FINISHED (%s → %s)",
            len(matches), date_from, date_to,
        )
        return matches

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        """ESPN no tiene un endpoint de eventos por match individual.
        Los eventos vienen embebidos en el scoreboard cuando el partido tiene detalles."""
        logger.info(
            "ESPN scraper: get_match_events no soportado directamente para match %s",
            match_external_id,
        )
        return []

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        payload = self.client.get_teams()
        teams: list[CanonicalTeam] = []

        # Estructura de standings: children[] → standings[] → entries[] → team
        for child in payload.get("children", []):
            for standing in child.get("standings", {}).get("entries", []):
                teams.append(EspnScraperMapper.map_team(standing))

        if not teams:
            # Fallback: buscar en estructura alternativa
            for standing in payload.get("standings", []):
                for entry in standing.get("entries", []):
                    teams.append(EspnScraperMapper.map_team(entry))

        logger.info("ESPN scraper teams: %d equipos", len(teams))
        return teams

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        payload = self.client.get_league_info()
        for league_data in payload.get("leagues", []):
            return EspnScraperMapper.map_league(league_data)
        return None

    def _get_league_name(self) -> str | None:
        """Cache simple para obtener el nombre de la liga."""
        if not hasattr(self, "_cached_league_name"):
            try:
                lg = self.get_league(0, 0)
                self._cached_league_name: str | None = lg.name if lg else None
            except Exception:
                self._cached_league_name = None
        return self._cached_league_name
