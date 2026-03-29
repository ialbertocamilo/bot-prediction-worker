"""PayPal REST API v2 implementation of PaymentProvider.

Uses Orders API for Guest Checkout (pay with card, no PayPal account required).
Docs: https://developer.paypal.com/docs/api/orders/v2/
"""
from __future__ import annotations

import logging
import os
from base64 import b64encode

import httpx

from config import APP_BASE_URL
from app.services.payments.base import (
    Gateway,
    GatewayPricing,
    PaymentItem,
    PaymentProvider,
    PaymentResult,
)

logger = logging.getLogger(__name__)

_PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "")
_PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
_PAYPAL_SANDBOX: bool = os.getenv("PAYPAL_SANDBOX", "true").lower() in ("true", "1", "yes")

_PAYPAL_PRICE: float = float(os.getenv("PAYPAL_ITEM_PRICE", "14.99"))
_PAYPAL_CURRENCY: str = os.getenv("PAYPAL_ITEM_CURRENCY", "USD")

_BASE_URL_SANDBOX = "https://api-m.sandbox.paypal.com"
_BASE_URL_LIVE = "https://api-m.paypal.com"

_TOKEN_TIMEOUT = 10.0
_API_TIMEOUT = 15.0


class PayPalAuthError(RuntimeError):
    """Raised when PayPal OAuth2 token request fails."""


class PayPalAPIError(RuntimeError):
    """Raised when a PayPal API call returns a non-success status."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(f"PayPal API error HTTP {status}: {detail}")


class PayPalProvider(PaymentProvider):
    """PayPal Orders API v2 — Guest Checkout (card without account)."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client_id: str = client_id or _PAYPAL_CLIENT_ID
        self._client_secret: str = client_secret or _PAYPAL_CLIENT_SECRET
        if not self._client_id or not self._client_secret:
            raise RuntimeError(
                "PAYPAL_CLIENT_ID y PAYPAL_CLIENT_SECRET deben estar configurados en .env"
            )
        self._base_url: str = _BASE_URL_SANDBOX if _PAYPAL_SANDBOX else _BASE_URL_LIVE

    @property
    def gateway(self) -> Gateway:
        return Gateway.PAYPAL

    @property
    def pricing(self) -> GatewayPricing:
        return GatewayPricing(
            price=_PAYPAL_PRICE,
            currency=_PAYPAL_CURRENCY,
            label=f"${_PAYPAL_PRICE:.2f} USD",
        )

    # ── OAuth2 token ──────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        credentials: str = b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )

        if resp.status_code != 200:
            logger.error("PayPal OAuth2 failed (HTTP %d): %s", resp.status_code, resp.text)
            raise PayPalAuthError(f"PayPal OAuth2 error: HTTP {resp.status_code}")

        body: dict = resp.json()
        token: str = body.get("access_token", "")
        if not token:
            raise PayPalAuthError("PayPal OAuth2 returned empty access_token")
        return token

    # ── PaymentProvider interface ─────────────────────────────────────

    async def create_preference(
        self,
        items: list[PaymentItem],
        external_reference: str,
    ) -> str:
        access_token: str = await self._get_access_token()

        pp_items: list[dict] = [
            {
                "name": item.title[:127],
                "quantity": str(item.quantity),
                "unit_amount": {
                    "currency_code": item.currency_id,
                    "value": f"{item.unit_price:.2f}",
                },
            }
            for item in items
        ]

        total: float = sum(
            float(item.unit_price) * item.quantity for item in items
        )

        order_payload: dict = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": external_reference,
                    "description": f"FútbolQuant créditos (ref: {external_reference})",
                    "amount": {
                        "currency_code": items[0].currency_id if items else _PAYPAL_CURRENCY,
                        "value": f"{total:.2f}",
                        "breakdown": {
                            "item_total": {
                                "currency_code": items[0].currency_id if items else _PAYPAL_CURRENCY,
                                "value": f"{total:.2f}",
                            },
                        },
                    },
                    "items": pp_items,
                },
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "payment_method_preference": "UNRESTRICTED",
                        "brand_name": "FútbolQuant",
                        "locale": "es-PE",
                        "landing_page": "GUEST_CHECKOUT",
                        "user_action": "PAY_NOW",
                        "return_url": f"{APP_BASE_URL}/payments/result?status=ok",
                        "cancel_url": f"{APP_BASE_URL}/payments/result?status=fail",
                    },
                },
            },
        }

        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                json=order_payload,
            )

        if resp.status_code not in (200, 201):
            logger.error("PayPal create order failed (HTTP %d): %s", resp.status_code, resp.text)
            raise PayPalAPIError(resp.status_code, resp.text[:300])

        body: dict = resp.json()
        order_id: str = body.get("id", "")

        approve_url: str = ""
        for link in body.get("links", []):
            if link.get("rel") == "payer-action":
                approve_url = link["href"]
                break

        if not approve_url:
            for link in body.get("links", []):
                if link.get("rel") == "approve":
                    approve_url = link["href"]
                    break

        if not approve_url:
            logger.error("PayPal order %s sin approve URL — body: %s", order_id, body)
            raise RuntimeError("PayPal no devolvió URL de aprobación")

        logger.info(
            "PayPal order created → order_id=%s (sandbox=%s)", order_id, _PAYPAL_SANDBOX,
        )
        return approve_url

    async def verify_payment(self, payment_id: str) -> PaymentResult:
        access_token: str = await self._get_access_token()

        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            capture_resp = await client.post(
                f"{self._base_url}/v2/checkout/orders/{payment_id}/capture",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

        if capture_resp.status_code == 422:
            async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
                get_resp = await client.get(
                    f"{self._base_url}/v2/checkout/orders/{payment_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if get_resp.status_code != 200:
                raise PayPalAPIError(get_resp.status_code, get_resp.text[:300])
            body = get_resp.json()
        elif capture_resp.status_code in (200, 201):
            body = capture_resp.json()
        else:
            logger.error(
                "PayPal capture failed (HTTP %d): %s",
                capture_resp.status_code,
                capture_resp.text[:300],
            )
            raise PayPalAPIError(capture_resp.status_code, capture_resp.text[:300])

        pp_status: str = body.get("status", "")
        status_map: dict[str, str] = {
            "COMPLETED": "approved",
            "APPROVED": "approved",
            "CREATED": "pending",
            "PAYER_ACTION_REQUIRED": "pending",
            "VOIDED": "rejected",
        }
        normalized_status: str = status_map.get(pp_status, "pending")

        purchase_units: list[dict] = body.get("purchase_units", [{}])
        first_unit: dict = purchase_units[0] if purchase_units else {}
        external_ref: str = first_unit.get("reference_id", "")
        amount_str: str = first_unit.get("amount", {}).get("value", "0")

        return PaymentResult(
            payment_id=body.get("id", payment_id),
            status=normalized_status,
            external_reference=external_ref,
            amount=float(amount_str),
        )
