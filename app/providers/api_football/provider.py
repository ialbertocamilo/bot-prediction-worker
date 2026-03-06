from __future__ import annotations

from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalTeam,
)
from app.providers.api_football.client import ApiFootballClient
from app.providers.api_football.mapper import ApiFootballMapper
from app.providers.base import BaseProvider


class ApiFootballProvider(BaseProvider):
    def __init__(self, client: ApiFootballClient | None = None) -> None:
        self.client: ApiFootballClient = client or ApiFootballClient()

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        payload: dict[str, Any] = self.client.get_fixtures(
            league_id=league_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
        )
        response_items: list[dict[str, Any]] = payload.get("response", [])
        return [ApiFootballMapper.map_match(item) for item in response_items]

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        payload: dict[str, Any] = self.client.get_results(
            league_id=league_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
        )
        response_items: list[dict[str, Any]] = payload.get("response", [])
        return [ApiFootballMapper.map_match(item) for item in response_items]

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        payload: dict[str, Any] = self.client.get_match_events(
            match_external_id=match_external_id,
        )
        response_items: list[dict[str, Any]] = payload.get("response", [])

        events: list[CanonicalMatchEvent] = []
        for item in response_items:
            event: CanonicalMatchEvent = ApiFootballMapper.map_event(item)
            event = event.model_copy(
                update={
                    "match_external_id": match_external_id,
                }
            )
            events.append(event)

        return events

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        payload: dict[str, Any] = self.client.get_teams(
            league_id=league_id,
            season=season,
        )
        response_items: list[dict[str, Any]] = payload.get("response", [])
        return [ApiFootballMapper.map_team(item) for item in response_items]

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        payload: dict[str, Any] = self.client.get_league(
            league_id=league_id,
            season=season,
        )
        response_items: list[dict[str, Any]] = payload.get("response", [])

        if not response_items:
            return None

        return ApiFootballMapper.map_league(response_items[0])