from __future__ import annotations

from app.providers.base import BaseProvider


class ProviderFactory:
    @staticmethod
    def create(provider_name: str) -> BaseProvider:
        normalized_name: str = provider_name.strip().lower()

        if normalized_name == "api-football":
            from app.providers.api_football.provider import ApiFootballProvider
            return ApiFootballProvider()

        if normalized_name == "football-data-org":
            from app.providers.football_data_org.provider import FootballDataOrgProvider
            return FootballDataOrgProvider()

        if normalized_name == "espn-scraper":
            from app.providers.espn_scraper.provider import EspnScraperProvider
            return EspnScraperProvider()

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