"""Mercado Pago implementation of PaymentProvider."""
from __future__ import annotations

import asyncio
import logging
import os

import mercadopago  # type: ignore[import-untyped]

from config import APP_BASE_URL
from app.services.payments.base import (
    Gateway,
    GatewayPricing,
    PaymentItem,
    PaymentProvider,
    PaymentResult,
)

logger = logging.getLogger(__name__)

_MP_ACCESS_TOKEN: str = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
_SANDBOX: bool = os.getenv("MP_SANDBOX", "true").lower() in ("true", "1", "yes")

_MP_PRICE: float = float(os.getenv("MP_ITEM_PRICE", "50.00"))
_MP_CURRENCY: str = os.getenv("MP_ITEM_CURRENCY", "PEN")


class MercadoPagoProvider(PaymentProvider):
    """Mercado Pago Checkout Pro — SDK v2."""

    def __init__(self, access_token: str | None = None) -> None:
        token = access_token or _MP_ACCESS_TOKEN
        if not token:
            raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN no configurado en .env")
        self._sdk: mercadopago.SDK = mercadopago.SDK(token)

    @property
    def gateway(self) -> Gateway:
        return Gateway.MERCADOPAGO

    @property
    def pricing(self) -> GatewayPricing:
        return GatewayPricing(
            price=_MP_PRICE,
            currency=_MP_CURRENCY,
            label=f"S/ {_MP_PRICE:.2f}",
        )

    # ── PaymentProvider interface ─────────────────────────────────────

    async def create_preference(
        self,
        items: list[PaymentItem],
        external_reference: str,
    ) -> str:
        mp_items: list[dict] = [
            {
                "title": item.title,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "currency_id": str(item.currency_id),
            }
            for item in items
        ]

        preference_data: dict = {
            "items": mp_items,
            "external_reference": external_reference,
            "notification_url": f"{APP_BASE_URL}/payments/webhook/mercadopago",
            "back_urls": {
                "success": f"{APP_BASE_URL}/payments/result?status=ok",
                "failure": f"{APP_BASE_URL}/payments/result?status=fail",
                "pending": f"{APP_BASE_URL}/payments/result?status=pending",
            },
            "auto_return": "approved",
        }

        try:
            response = await asyncio.to_thread(
                self._sdk.preference().create, preference_data
            )
        except Exception as exc:
            logger.exception("MP SDK preference().create raised: %s", exc)
            raise RuntimeError(f"Error de red/SDK al crear preference: {exc}") from exc

        status_code: int = response.get("status", 0)
        if status_code != 201:
            logger.error("MP create_preference failed (HTTP %s): %s", status_code, response)
            raise RuntimeError(f"Mercado Pago error: HTTP {status_code}")

        body: dict = response["response"]
        preference_id: str = body.get("id", "")

        init_point: str = (
            body.get("sandbox_init_point", "") if _SANDBOX else body.get("init_point", "")
        )
        if not init_point:
            logger.error("MP preference %s sin init_point — body: %s", preference_id, body)
            raise RuntimeError("Mercado Pago no devolvió init_point")

        logger.info(
            "MP preference created → pref_id=%s (sandbox=%s)", preference_id, _SANDBOX
        )
        return init_point

    async def verify_payment(self, payment_id: str) -> PaymentResult:
        try:
            response = await asyncio.to_thread(
                self._sdk.payment().get, payment_id
            )
        except Exception as exc:
            logger.exception("MP SDK payment().get raised: %s", exc)
            raise RuntimeError(f"Error de red/SDK al consultar pago: {exc}") from exc

        if response["status"] != 200:
            logger.error("MP get_payment failed: %s", response)
            raise RuntimeError(f"Mercado Pago error: {response['status']}")

        body: dict = response["response"]
        return PaymentResult(
            payment_id=str(body.get("id", payment_id)),
            status=body.get("status", ""),
            external_reference=str(body.get("external_reference", "")),
            amount=float(body.get("transaction_amount", 0)),
        )
