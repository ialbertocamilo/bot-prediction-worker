"""Abstract base for payment providers (Strategy Pattern)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Gateway(str, Enum):
    """Supported payment gateways."""

    MERCADOPAGO = "mercadopago"
    PAYPAL = "paypal"


@dataclass(frozen=True, slots=True)
class PaymentItem:
    """A single line-item sent to the payment gateway."""

    title: str
    quantity: int
    unit_price: float
    currency_id: str


@dataclass(frozen=True, slots=True)
class PaymentResult:
    """Verified payment data returned by the provider."""

    payment_id: str
    status: str  # "approved", "pending", "rejected", …
    external_reference: str  # telegram_id encoded as str
    amount: float


@dataclass(frozen=True, slots=True)
class GatewayPricing:
    """Price + currency for a specific gateway."""

    price: float
    currency: str
    label: str  # e.g. "S/ 50.00" or "$14.99"


class PaymentProvider(ABC):
    """Contract every payment gateway must implement."""

    @property
    @abstractmethod
    def gateway(self) -> Gateway:
        """Identifier for this provider."""

    @property
    @abstractmethod
    def pricing(self) -> GatewayPricing:
        """Price configuration for this gateway."""

    @abstractmethod
    async def create_preference(
        self,
        items: list[PaymentItem],
        external_reference: str,
    ) -> str:
        """Build a checkout preference and return the payment URL.

        Raises ``RuntimeError`` on gateway errors.
        """

    @abstractmethod
    async def verify_payment(self, payment_id: str) -> PaymentResult:
        """Query the gateway for the payment status.

        Raises ``RuntimeError`` if the payment cannot be found or the
        gateway is unreachable.
        """
