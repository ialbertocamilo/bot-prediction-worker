from __future__ import annotations

from app.providers.api_football.provider import ApiFootballProvider
from app.providers.base import BaseProvider


class ProviderFactory:
    @staticmethod
    def create(provider_name: str) -> BaseProvider:
        normalized_name: str = provider_name.strip().lower()

        if normalized_name == "api-football":
            return ApiFootballProvider()

        raise ValueError(f"Proveedor no soportado: {provider_name}")