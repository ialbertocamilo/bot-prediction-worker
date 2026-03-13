from __future__ import annotations

from typing import Any

from app.providers.base import BaseProvider


class ProviderFactory:
    @staticmethod
    def create(provider_name: str, **kwargs: Any) -> BaseProvider:
        """Create a provider instance by name.

        Keyword arguments are forwarded to the provider constructor
        when applicable (e.g., league_slug for ESPN).
        """
        normalized_name: str = provider_name.strip().lower()
        league_slug = kwargs.get("league_slug")

        if normalized_name == "api-football":
            from app.providers.api_football.provider import ApiFootballProvider
            return ApiFootballProvider()

        if normalized_name == "football-data-org":
            from app.providers.football_data_org.provider import FootballDataOrgProvider
            return FootballDataOrgProvider()

        if normalized_name == "espn-scraper":
            from app.providers.espn_scraper.client import EspnScraperClient
            from app.providers.espn_scraper.provider import EspnScraperProvider
            client = EspnScraperClient(league_slug=league_slug) if league_slug else EspnScraperClient()
            return EspnScraperProvider(client=client)

        if normalized_name == "composite":
            from app.providers.composite import CompositeProvider
            return CompositeProvider()

        if normalized_name == "transfermarkt":
            from app.providers.transfermarkt.provider import TransfermarktProvider
            return TransfermarktProvider()

        if normalized_name == "sofascore":
            from app.providers.sofascore.provider import SofaScoreProvider
            return SofaScoreProvider()

        raise ValueError(f"Proveedor no soportado: {provider_name}")