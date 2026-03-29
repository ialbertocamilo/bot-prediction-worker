"""Servicio de Mercado Pago — genera preferences y consulta pagos."""
from __future__ import annotations

import asyncio
import logging
import os

import mercadopago  # type: ignore[import-untyped]

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MP_ACCESS_TOKEN: str = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
_WEBHOOK_BASE_URL: str = os.getenv("APP_BASE_URL", "").rstrip("/")
_SANDBOX: bool = os.getenv("MP_SANDBOX", "true").lower() in ("true", "1", "yes")

# Créditos y precio por defecto
CREDITS_PER_PURCHASE: int = int(os.getenv("MP_CREDITS_PER_PURCHASE", "50"))
ITEM_PRICE: float = float(os.getenv("MP_ITEM_PRICE", "500.00"))
ITEM_CURRENCY: str = os.getenv("MP_ITEM_CURRENCY", "PEN")


def _get_sdk() -> mercadopago.SDK:
    """Instancia el SDK v2 de Mercado Pago. Falla si no hay token."""
    if not _MP_ACCESS_TOKEN:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN no configurado en .env")
    return mercadopago.SDK(_MP_ACCESS_TOKEN)


async def create_preference(telegram_id: int) -> str:
    """Crea una preference en MP y devuelve el init_point (link de pago).

    Usa ``external_reference`` = telegram_id para vincular el pago al usuario.
    """
    sdk = _get_sdk()

    if not _WEBHOOK_BASE_URL:
        raise RuntimeError(
            "APP_BASE_URL no configurado en .env — "
            "back_urls requiere una URL pública (ej. ngrok)"
        )

    # ── Tipado fuerte: unit_price DEBE ser float, no str ni Decimal ──
    unit_price: float = float(ITEM_PRICE)

    preference_data: dict = {
        "items": [
            {
                "title": f"FútbolQuant — {CREDITS_PER_PURCHASE} créditos",
                "quantity": 1,
                "unit_price": unit_price,
                "currency_id": str(ITEM_CURRENCY),  # ← moneda explícita ("PEN")
            }
        ],
        "external_reference": str(telegram_id),
        "notification_url": f"{_WEBHOOK_BASE_URL}/payments/webhook",
        # ── back_urls obligatorias para que Checkout Pro no deshabilite el botón ──
        "back_urls": {
            "success": f"{_WEBHOOK_BASE_URL}/payments/result?status=ok",
            "failure": f"{_WEBHOOK_BASE_URL}/payments/result?status=fail",
            "pending": f"{_WEBHOOK_BASE_URL}/payments/result?status=pending",
        },
        "auto_return": "approved",
    }

    try:
        response = await asyncio.to_thread(
            sdk.preference().create, preference_data
        )
    except Exception as exc:
        logger.exception("MP SDK preference().create raised: %s", exc)
        raise RuntimeError(f"Error de red/SDK al crear preference: {exc}") from exc

    status_code: int = response.get("status", 0)
    if status_code != 201:
        logger.error(
            "MP create_preference failed (HTTP %s): %s", status_code, response
        )
        raise RuntimeError(f"Mercado Pago error: HTTP {status_code}")

    body: dict = response["response"]
    preference_id: str = body.get("id", "")

    # Sandbox devuelve sandbox_init_point; producción devuelve init_point
    init_point: str = (
        body.get("sandbox_init_point", "") if _SANDBOX else body.get("init_point", "")
    )

    if not init_point:
        logger.error(
            "MP preference %s creada pero sin init_point — body: %s",
            preference_id,
            body,
        )
        raise RuntimeError("Mercado Pago no devolvió init_point")

    logger.info(
        "MP preference created for tg_id=%d → preference_id=%s (sandbox=%s)",
        telegram_id,
        preference_id,
        _SANDBOX,
    )
    return init_point


async def get_payment(payment_id: str) -> dict:
    """Consulta un pago en MP por su ID y devuelve el dict completo."""
    sdk = _get_sdk()
    try:
        response = await asyncio.to_thread(
            sdk.payment().get, payment_id
        )
    except Exception as exc:
        logger.exception("MP SDK payment().get raised: %s", exc)
        raise RuntimeError(f"Error de red/SDK al consultar pago: {exc}") from exc

    if response["status"] != 200:
        logger.error("MP get_payment failed: %s", response)
        raise RuntimeError(f"Mercado Pago error: {response['status']}")
    return response["response"]
