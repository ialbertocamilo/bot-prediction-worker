from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalTeam,
)
from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class CompositeProvider(BaseProvider):
    """Provider compuesto que rutea a diferentes providers según la liga.

    Config via variables de entorno:
        COMPOSITE_ROUTES = "per.1:espn-scraper,2021:football-data-org"

    Formato: "league_id_o_slug:provider_name,..."
    Si un league_id no tiene ruta, usa el provider por defecto.
    """

    def __init__(
        self,
        routes: dict[str, BaseProvider] | None = None,
        default_provider: BaseProvider | None = None,
    ) -> None:
        from app.providers.factory import ProviderFactory

        if routes is not None:
            self._routes = routes
            self._default = default_provider
        else:
            self._routes, self._default = self._build_from_env(ProviderFactory)

    @staticmethod
    def _build_from_env(
        factory: type,
    ) -> tuple[dict[str, BaseProvider], BaseProvider | None]:
        """Construye rutas desde COMPOSITE_ROUTES y COMPOSITE_DEFAULT."""
        routes_str = os.getenv("COMPOSITE_ROUTES", "")
        default_name = os.getenv("COMPOSITE_DEFAULT", "")

        provider_cache: dict[str, BaseProvider] = {}
        routes: dict[str, BaseProvider] = {}

        for pair in routes_str.split(","):
            pair = pair.strip()
            if ":" not in pair:
                continue
            league_key, provider_name = pair.split(":", 1)
            league_key = league_key.strip()
            provider_name = provider_name.strip()

            if provider_name not in provider_cache:
                provider_cache[provider_name] = factory.create(provider_name)
            routes[league_key] = provider_cache[provider_name]

        default: BaseProvider | None = None
        if default_name:
            if default_name not in provider_cache:
                provider_cache[default_name] = factory.create(default_name)
            default = provider_cache[default_name]

        logger.info(
            "CompositeProvider: %d rutas configuradas, default=%s",
            len(routes), default_name or "None",
        )
        return routes, default

    def _resolve(self, league_id: int) -> BaseProvider:
        """Busca el provider adecuado para un league_id."""
        key = str(league_id)
        if key in self._routes:
            return self._routes[key]
        if self._default:
            return self._default
        raise ValueError(
            f"CompositeProvider: no hay ruta para league_id={league_id} "
            f"y no hay default configurado. "
            f"Rutas disponibles: {list(self._routes.keys())}"
        )

    # ── Delegación ──────────────────────────────────────────────

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        provider = self._resolve(league_id)
        logger.debug(
            "CompositeProvider: fixtures league=%d → %s",
            league_id, type(provider).__name__,
        )
        return provider.get_fixtures(league_id, season, date_from, date_to)

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        provider = self._resolve(league_id)
        return provider.get_results(league_id, season, date_from, date_to)

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        # Para eventos, usar el default o el primero disponible
        if self._default:
            return self._default.get_match_events(match_external_id)
        for provider in self._routes.values():
            return provider.get_match_events(match_external_id)
        return []

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        provider = self._resolve(league_id)
        return provider.get_teams(league_id, season)

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        provider = self._resolve(league_id)
        return provider.get_league(league_id, season)
