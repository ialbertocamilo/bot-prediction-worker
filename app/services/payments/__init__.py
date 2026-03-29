"""Payment services — Strategy Pattern for multi-gateway support."""
from app.services.payments.base import (
    Gateway,
    GatewayPricing,
    PaymentItem,
    PaymentProvider,
    PaymentResult,
)
from app.services.payments.factory import PaymentFactory
from app.services.payments.mercadopago_provider import MercadoPagoProvider
from app.services.payments.paypal_provider import PayPalProvider
from app.services.payments.payment_service import PaymentService

__all__ = [
    "Gateway",
    "GatewayPricing",
    "PaymentFactory",
    "PaymentItem",
    "PaymentProvider",
    "PaymentResult",
    "MercadoPagoProvider",
    "PayPalProvider",
    "PaymentService",
]
