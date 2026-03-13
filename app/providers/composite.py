from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalMatchStats,
    CanonicalOdds,
    CanonicalTeam,
)
from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class CompositeProvider(BaseProvider):
    """Provider compuesto que rutea a diferentes providers según la liga.

    Supports:
    - League-based routing via COMPOSITE_ROUTES
    - Automatic fallback: if primary fails, try secondary/default providers
    - Stats providers: dedicated provider for match statistics

    Config via variables de entorno:
        COMPOSITE_ROUTES = "per.1:espn-scraper,2021:football-data-org"
        COMPOSITE_DEFAULT = "espn-scraper"
        STATS_PROVIDER = "sofascore"

    Formato: "league_id_o_slug:provider_name,..."
    """

    def __init__(
        self,
        routes: dict[str, BaseProvider] | None = None,
        default_provider: BaseProvider | None = None,
        fallback_providers: list[BaseProvider] | None = None,
        stats_provider: BaseProvider | None = None,
    ) -> None:
        from app.providers.factory import ProviderFactory

        if routes is not None:
            self._routes = routes
            self._default = default_provider
            self._fallbacks = fallback_providers or []
            self._stats_provider = stats_provider
        else:
            self._routes, self._default, self._fallbacks, self._stats_provider = (
                self._build_from_env(ProviderFactory)
            )

    @staticmethod
    def _build_from_env(
        factory: type,
    ) -> tuple[dict[str, BaseProvider], BaseProvider | None, list[BaseProvider], BaseProvider | None]:
        """Construye rutas desde COMPOSITE_ROUTES, COMPOSITE_DEFAULT, and STATS_PROVIDER."""
        routes_str = os.getenv("COMPOSITE_ROUTES", "")
        default_name = os.getenv("COMPOSITE_DEFAULT", "")
        fallback_names = os.getenv("COMPOSITE_FALLBACKS", "")
        stats_name = os.getenv("STATS_PROVIDER", "")

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

        fallbacks: list[BaseProvider] = []
        for fname in fallback_names.split(","):
            fname = fname.strip()
            if not fname:
                continue
            if fname not in provider_cache:
                provider_cache[fname] = factory.create(fname)
            fallbacks.append(provider_cache[fname])

        stats_provider: BaseProvider | None = None
        if stats_name:
            if stats_name not in provider_cache:
                provider_cache[stats_name] = factory.create(stats_name)
            stats_provider = provider_cache[stats_name]

        logger.info(
            "CompositeProvider: %d rutas, default=%s, fallbacks=%d, stats=%s",
            len(routes), default_name or "None", len(fallbacks), stats_name or "None",
        )
        return routes, default, fallbacks, stats_provider

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

    def _resolve_with_fallbacks(self, league_id: int) -> list[BaseProvider]:
        """Return ordered list: primary → fallbacks."""
        providers: list[BaseProvider] = []
        try:
            providers.append(self._resolve(league_id))
        except ValueError:
            pass
        for fb in self._fallbacks:
            if fb not in providers:
                providers.append(fb)
        if self._default and self._default not in providers:
            providers.append(self._default)
        return providers

    # ── Delegación con fallback ─────────────────────────────────

    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        for provider in self._resolve_with_fallbacks(league_id):
            try:
                result = provider.get_fixtures(league_id, season, date_from, date_to)
                if result:
                    logger.debug(
                        "CompositeProvider: fixtures league=%d → %s (%d)",
                        league_id, type(provider).__name__, len(result),
                    )
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for fixtures league=%d, trying next",
                    type(provider).__name__, league_id,
                )
        return []

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        for provider in self._resolve_with_fallbacks(league_id):
            try:
                result = provider.get_results(league_id, season, date_from, date_to)
                if result:
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for results league=%d, trying next",
                    type(provider).__name__, league_id,
                )
        return []

    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        providers: list[BaseProvider] = []
        if self._default:
            providers.append(self._default)
        for fb in self._fallbacks:
            if fb not in providers:
                providers.append(fb)
        for p in self._routes.values():
            if p not in providers:
                providers.append(p)

        for provider in providers:
            try:
                result = provider.get_match_events(match_external_id)
                if result:
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for match_events %s, trying next",
                    type(provider).__name__, match_external_id,
                )
        return []

    def get_match_stats(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchStats]:
        # Prefer dedicated stats provider, then fallback to others
        if self._stats_provider:
            try:
                result = self._stats_provider.get_match_stats(match_external_id)
                if result:
                    return result
            except Exception:
                logger.warning("CompositeProvider: stats provider failed for %s", match_external_id)
        # Try other providers
        for provider in [self._default, *self._routes.values()]:
            if provider and provider is not self._stats_provider:
                try:
                    result = provider.get_match_stats(match_external_id)
                    if result:
                        return result
                except Exception:
                    continue
        return []

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        for provider in self._resolve_with_fallbacks(league_id):
            try:
                result = provider.get_teams(league_id, season)
                if result:
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for teams league=%d, trying next",
                    type(provider).__name__, league_id,
                )
        return []

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        for provider in self._resolve_with_fallbacks(league_id):
            try:
                result = provider.get_league(league_id, season)
                if result:
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for league %d, trying next",
                    type(provider).__name__, league_id,
                )
        return None

    def get_odds(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalOdds]:
        for provider in self._resolve_with_fallbacks(league_id):
            try:
                result = provider.get_odds(league_id, season, date_from, date_to)
                if result:
                    return result
            except Exception:
                logger.warning(
                    "CompositeProvider: %s failed for odds league=%d, trying next",
                    type(provider).__name__, league_id,
                )
        return []
