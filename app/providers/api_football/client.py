from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests


class ApiFootballClient:
    BASE_URL: str = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str | None = None) -> None:
        resolved_api_key: str | None = api_key or os.getenv("API_FOOTBALL_KEY")
        if resolved_api_key is None:
            raise RuntimeError("API_FOOTBALL_KEY no está definida en el entorno")

        self.api_key: str = resolved_api_key

    def _get(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response: requests.Response = requests.get(
            f"{self.BASE_URL}{path}",
            headers={"x-apisports-key": self.api_key},
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        errors: Any = payload.get("errors")
        if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and len(errors) > 0):
            raise RuntimeError(f"API-Football devolvió errores: {errors}")

        return payload

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        return self._get(
            path="/fixtures",
            params={
                "league": league_id,
                "season": season,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            },
        )

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        return self._get(
            path="/fixtures",
            params={
                "league": league_id,
                "season": season,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "status": "FT",
            },
        )

    def get_match_events(
        self,
        match_external_id: str,
    ) -> dict[str, Any]:
        return self._get(
            path="/fixtures/events",
            params={"fixture": match_external_id},
        )

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> dict[str, Any]:
        return self._get(
            path="/teams",
            params={
                "league": league_id,
                "season": season,
            },
        )

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> dict[str, Any]:
        return self._get(
            path="/leagues",
            params={
                "id": league_id,
                "season": season,
            },
        )