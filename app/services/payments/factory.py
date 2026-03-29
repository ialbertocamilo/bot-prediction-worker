"""PaymentFactory — resolves the correct provider by gateway selection."""
from __future__ import annotations

from app.services.payments.base import Gateway, PaymentProvider
from app.services.payments.mercadopago_provider import MercadoPagoProvider
from app.services.payments.paypal_provider import PayPalProvider


class PaymentFactory:
    """Instantiates the right ``PaymentProvider`` for a given gateway."""

    _registry: dict[Gateway, type[PaymentProvider]] = {
        Gateway.MERCADOPAGO: MercadoPagoProvider,
        Gateway.PAYPAL: PayPalProvider,
    }

    @classmethod
    def create(cls, gateway: Gateway | str) -> PaymentProvider:
        if isinstance(gateway, str):
            gateway = Gateway(gateway.lower())
        provider_cls = cls._registry.get(gateway)
        if provider_cls is None:
            raise ValueError(f"Gateway no soportado: {gateway!r}")
        return provider_cls()

    @classmethod
    def available_gateways(cls) -> list[Gateway]:
        return list(cls._registry.keys())
