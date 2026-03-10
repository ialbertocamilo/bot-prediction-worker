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
from app.providers.base import BaseProvider
from app.providers.football_data_org.client import FootballDataOrgClient
from app.providers.football_data_org.mapper import FootballDataOrgMapper

logger = logging.getLogger(__name__)


class FootballDataOrgProvider(BaseProvider):
    """Provider para football-data.org v4 (plan gratuito).

    Competiciones disponibles en el plan gratuito:
        PL  = 2021  (Premier League)
        BL1 = 2002  (Bundesliga)
        PD  = 2014  (La Liga)
        SA  = 2019  (Serie A)
        FL1 = 2015  (Ligue 1)
        DED = 2003  (Eredivisie)
        PPL = 2017  (Primeira Liga)
        ELC = 2016  (Championship)
        CL  = 2001  (Champions League)
        BSA = 2013  (Brasileirão Série A)
        WC  = 2000  (World Cup)
        EC  = 2018  (European Championship)

    Usa el ID numérico como league_id (ej: 2021 para PL).
    """

    def __init__(self, client: FootballDataOrgClient | None = None) -> None:
        self.client: FootballDataOrgClient = client or FootballDataOrgClient()

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        payload: dict[str, Any] = self.client.get_fixtures(
            competition_id=league_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
        )
        raw_matches: list[dict[str, Any]] = payload.get("matches", [])
        logger.info(
            "football-data.org fixtures: %d partidos (league=%d, %s → %s)",
            len(raw_matches), league_id, date_from, date_to,
        )
        return [FootballDataOrgMapper.map_match(m) for m in raw_matches]

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        payload: dict[str, Any] = self.client.get_results(
            competition_id=league_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
        )
        raw_matches: list[dict[str, Any]] = payload.get("matches", [])
        logger.info(
            "football-data.org results: %d partidos (league=%d, %s → %s)",
            len(raw_matches), league_id, date_from, date_to,
        )
        return [FootballDataOrgMapper.map_match(m) for m in raw_matches]

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        """Obtiene goles de un partido. El plan gratuito no tiene eventos
        detallados (tarjetas, subs), solo goles desde el match detail."""
        try:
            payload: dict[str, Any] = self.client.get_match(
                match_id=int(match_external_id),
            )
            goals: list[dict[str, Any]] = payload.get("goals", [])
            return [
                FootballDataOrgMapper.map_event(g, match_external_id)
                for g in goals
            ]
        except Exception:
            logger.warning(
                "football-data.org: no se pudieron obtener eventos para match %s",
                match_external_id,
            )
            return []

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        payload: dict[str, Any] = self.client.get_teams(
            competition_id=league_id,
            season=season,
        )
        raw_teams: list[dict[str, Any]] = payload.get("teams", [])
        logger.info(
            "football-data.org teams: %d equipos (league=%d, season=%d)",
            len(raw_teams), league_id, season,
        )
        return [FootballDataOrgMapper.map_team(t) for t in raw_teams]

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        payload: dict[str, Any] = self.client.get_competition(
            competition_id=league_id,
        )
        return FootballDataOrgMapper.map_league(payload)
