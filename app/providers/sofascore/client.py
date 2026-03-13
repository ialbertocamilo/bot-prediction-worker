from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import requests

from app.providers.cache import ProviderCache, get_provider_cache
from app.providers.rate_limiter import RateLimiter, get_rate_limiter

logger = logging.getLogger(__name__)


class SofaScoreClient:
    """Cliente para la API pública de SofaScore.

    SofaScore expone endpoints JSON públicos para estadísticas de partidos.
    No requiere API key pero sí headers de navegador y rate-limit conservador.

    Configuración vía variables de entorno:
        SOFASCORE_TOURNAMENT_ID  – ID del torneo  (default: 406 = Liga 1 Perú)
        SOFASCORE_SEASON_ID      – ID de la temporada (ajustar por año)
    """

    BASE_URL: str = "https://api.sofascore.com/api/v1"
    _MIN_INTERVAL: float = 2.5  # segundos entre requests

    def __init__(
        self,
        tournament_id: int | None = None,
        season_id: int | None = None,
    ) -> None:
        self.tournament_id: int = tournament_id or int(
            os.getenv("SOFASCORE_TOURNAMENT_ID", "406")
        )
        self.season_id: int | None = season_id or (
            int(os.getenv("SOFASCORE_SEASON_ID")) if os.getenv("SOFASCORE_SEASON_ID") else None
        )
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.sofascore.com/",
                "Origin": "https://www.sofascore.com",
                "Cache-Control": "no-cache",
            }
        )
        self._limiter: RateLimiter = get_rate_limiter(
            "sofascore", min_interval=self._MIN_INTERVAL, session=session,
        )
        self._cache: ProviderCache = get_provider_cache()

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{path}"
        cache_key = self._cache.make_key("sofascore", path)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("SofaScore cache hit: %s", path)
            return cached

        logger.debug("SofaScore GET %s", url)
        resp = self._limiter.get(url, timeout=20)
        data = resp.json()
        self._cache.set(cache_key, data)
        return data

    # ── Estadísticas de un partido ──────────────────────────────

    def get_match_statistics(self, event_id: str) -> dict[str, Any]:
        """GET /event/{id}/statistics

        Retorna estadísticas agrupadas por período (ALL, 1H, 2H).
        """
        return self._get(f"event/{event_id}/statistics")

    # ── Detalle de un partido ───────────────────────────────────

    def get_match_detail(self, event_id: str) -> dict[str, Any]:
        """GET /event/{id}

        Retorna info general del partido: equipos, score, status.
        """
        return self._get(f"event/{event_id}")

    # ── Eventos (partidos) de una fecha ─────────────────────────

    def get_events_by_date(self, target_date: date) -> dict[str, Any]:
        """GET /sport/football/scheduled-events/{YYYY-MM-DD}

        Retorna todos los partidos de fútbol para esa fecha.
        """
        return self._get(
            f"sport/football/scheduled-events/{target_date.isoformat()}"
        )

    # ── Partidos de un torneo/temporada ─────────────────────────

    def get_tournament_events(
        self,
        page: int = 0,
        direction: str = "last",
    ) -> dict[str, Any]:
        """GET /unique-tournament/{id}/season/{sid}/events/{direction}/{page}

        Retorna partidos paginados de un torneo.
        ``direction``: 'last' para resultados, 'next' para fixtures.
        """
        if self.season_id is None:
            raise ValueError("SOFASCORE_SEASON_ID es requerido para consultas de torneo")
        return self._get(
            f"unique-tournament/{self.tournament_id}"
            f"/season/{self.season_id}"
            f"/events/{direction}/{page}"
        )

    # ── Lineups ─────────────────────────────────────────────────

    def get_match_lineups(self, event_id: str) -> dict[str, Any]:
        """GET /event/{id}/lineups

        Retorna alineaciones con datos de jugadores.
        """
        return self._get(f"event/{event_id}/lineups")
