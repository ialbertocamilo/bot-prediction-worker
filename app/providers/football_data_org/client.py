from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any

import requests

logger = logging.getLogger(__name__)


class FootballDataOrgClient:
    """Cliente HTTP para la API v4 de football-data.org (plan gratuito)."""

    BASE_URL: str = "https://api.football-data.org/v4"

    # Plan gratuito: 10 requests/minuto
    _MIN_INTERVAL: float = 6.5  # segundos entre requests para no exceder el límite

    def __init__(self, api_key: str | None = None) -> None:
        resolved_api_key: str | None = api_key or os.getenv("FOOTBALL_DATA_ORG_KEY")
        if resolved_api_key is None:
            raise RuntimeError(
                "FOOTBALL_DATA_ORG_KEY no está definida en el entorno. "
                "Regístrate gratis en https://www.football-data.org para obtener una."
            )
        self.api_key: str = resolved_api_key
        self._last_request_time: float = 0.0

    def _throttle(self) -> None:
        """Respeta el rate-limit del plan gratuito."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._MIN_INTERVAL:
            wait = self._MIN_INTERVAL - elapsed
            logger.debug("football-data.org: esperando %.1fs por rate-limit", wait)
            time.sleep(wait)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        url = f"{self.BASE_URL}{path}"
        response: requests.Response = requests.get(
            url,
            headers={"X-Auth-Token": self.api_key},
            params=params or {},
            timeout=20,
        )
        self._last_request_time = time.monotonic()

        if response.status_code == 429:
            raise RuntimeError(
                "football-data.org: rate-limit excedido. "
                "Plan gratuito permite 10 req/min."
            )
        response.raise_for_status()
        return response.json()

    # ── Fixtures / Matches ──────────────────────────────────────

    def get_matches(
        self,
        competition_id: int,
        season: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """GET /v4/competitions/{id}/matches con filtros opcionales."""
        params: dict[str, Any] = {}
        if season is not None:
            params["season"] = season
        if date_from is not None:
            params["dateFrom"] = date_from.isoformat()
        if date_to is not None:
            params["dateTo"] = date_to.isoformat()
        if status is not None:
            params["status"] = status
        return self._get(f"/competitions/{competition_id}/matches", params)

    def get_fixtures(
        self,
        competition_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        """Partidos programados (SCHEDULED + TIMED)."""
        return self.get_matches(
            competition_id=competition_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
            status="SCHEDULED,TIMED",
        )

    def get_results(
        self,
        competition_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        """Partidos finalizados (FINISHED)."""
        return self.get_matches(
            competition_id=competition_id,
            season=season,
            date_from=date_from,
            date_to=date_to,
            status="FINISHED",
        )

    # ── Teams ───────────────────────────────────────────────────

    def get_teams(
        self,
        competition_id: int,
        season: int,
    ) -> dict[str, Any]:
        """GET /v4/competitions/{id}/teams?season={year}."""
        return self._get(
            f"/competitions/{competition_id}/teams",
            params={"season": season},
        )

    # ── Competition (league) ────────────────────────────────────

    def get_competition(
        self,
        competition_id: int,
    ) -> dict[str, Any]:
        """GET /v4/competitions/{id}."""
        return self._get(f"/competitions/{competition_id}")

    # ── Match detail ────────────────────────────────────────────

    def get_match(self, match_id: int) -> dict[str, Any]:
        """GET /v4/matches/{id} — detalle de un partido."""
        return self._get(f"/matches/{match_id}")
