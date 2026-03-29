"""Payment webhooks — multi-gateway via Strategy Pattern."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.payments.base import PaymentResult
from app.services.payments.mercadopago_provider import MercadoPagoProvider
from app.services.payments.paypal_provider import PayPalProvider
from app.services.payments.payment_service import (
    CREDITS_PER_PURCHASE,
    PaymentAlreadyProcessed,
    PaymentNotApproved,
    PaymentService,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SANDBOX: bool = os.getenv("MP_SANDBOX", "true").lower() in ("true", "1", "yes")


# ── Pydantic: esquema Webhooks v2 de Mercado Pago ─────────────────────


class MPWebhookData(BaseModel):
    """Sub-objeto ``data`` del webhook — contiene el ID del recurso."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Payment ID de Mercado Pago")
    test_telegram_id: int | None = Field(
        default=None,
        description="(Solo sandbox) telegram_id para simular pago aprobado",
    )

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id_to_str(cls, v: object) -> str:
        return str(v)


class MPWebhookPayload(BaseModel):
    """Payload completo de un evento Webhooks v2 de Mercado Pago."""

    model_config = ConfigDict(extra="allow")

    action: str = Field(..., examples=["payment.created"])
    api_version: str = Field("v1", examples=["v1"])
    data: MPWebhookData
    date_created: str = Field(..., examples=["2026-03-28T10:55:00.000-04:00"])
    id: int = Field(..., description="ID de la notificación webhook")
    live_mode: bool = Field(False)
    type: str = Field(..., examples=["payment"])
    user_id: str = Field(..., description="ID del vendedor en Mercado Pago")

    @field_validator("user_id", mode="before")
    @classmethod
    def coerce_user_id_to_str(cls, v: object) -> str:
        return str(v)


# ── Mercado Pago webhook ─────────────────────────────────────────────


@router.post("/webhook/mercadopago")
async def mp_webhook(
    payload: MPWebhookPayload,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Recibe notificaciones de Mercado Pago (Webhooks v2).

    Delega la lógica de negocio a ``PaymentService`` con
    ``MercadoPagoProvider`` como estrategia.
    """
    if "payment" not in payload.action and payload.type != "payment":
        return JSONResponse({"ok": True}, status_code=200)

    payment_id: str = payload.data.id
    if not payment_id:
        return JSONResponse({"ok": True}, status_code=200)

    try:
        provider = MercadoPagoProvider()
        svc = PaymentService(db, provider)

        # Sandbox simulation
        simulated: PaymentResult | None = None
        if _SANDBOX and payload.data.test_telegram_id is not None:
            logger.info(
                "MP webhook: SANDBOX sim → payment=%s tg_id=%d",
                payment_id,
                payload.data.test_telegram_id,
            )
            simulated = PaymentResult(
                payment_id=payment_id,
                status="approved",
                external_reference=str(payload.data.test_telegram_id),
                amount=float(provider.pricing.price),
            )

        new_balance: int = await svc.process_approved_payment(
            payment_id, simulated=simulated,
        )
        return JSONResponse(
            {"ok": True, "balance": new_balance}, status_code=200,
        )

    except PaymentAlreadyProcessed:
        return JSONResponse(
            {"ok": True, "detail": "already_processed"}, status_code=200,
        )
    except PaymentNotApproved:
        return JSONResponse({"ok": True}, status_code=200)
    except ValueError as exc:
        logger.warning("MP webhook: %s", exc)
        return JSONResponse({"ok": True}, status_code=200)
    except Exception:
        logger.exception("MP webhook: error for payment %s", payment_id)
        db.rollback()
        return JSONResponse({"ok": True}, status_code=200)


# ── Legacy alias: keep /webhook working during migration ─────────────

@router.post("/webhook")
async def mp_webhook_legacy(
    payload: MPWebhookPayload,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Alias for ``/webhook/mercadopago`` — backwards-compatible."""
    return await mp_webhook(payload, db)


# ── PayPal webhook ────────────────────────────────────────────────────


class PayPalWebhookResource(BaseModel):
    """Sub-objeto ``resource`` del evento PayPal (campos mínimos)."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Order / Capture ID")
    status: str = Field("", description="COMPLETED, APPROVED, …")
    custom_id: str | None = Field(None, description="telegram_id passed at order creation")

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: object) -> str:
        return str(v)


class PayPalWebhookPayload(BaseModel):
    """Payload de un evento webhook de PayPal."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Webhook event ID")
    event_type: str = Field(..., examples=["CHECKOUT.ORDER.APPROVED"])
    resource: PayPalWebhookResource


@router.post("/webhook/paypal")
async def paypal_webhook(
    payload: PayPalWebhookPayload,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Recibe notificaciones webhook de PayPal.

    Events of interest: CHECKOUT.ORDER.APPROVED, PAYMENT.CAPTURE.COMPLETED.
    """
    event = payload.event_type.upper()
    if "APPROVED" not in event and "COMPLETED" not in event:
        return JSONResponse({"ok": True}, status_code=200)

    order_id: str = payload.resource.id
    if not order_id:
        return JSONResponse({"ok": True}, status_code=200)

    try:
        provider = PayPalProvider()
        svc = PaymentService(db, provider)

        new_balance: int = await svc.process_approved_payment(order_id)
        return JSONResponse(
            {"ok": True, "balance": new_balance}, status_code=200,
        )

    except PaymentAlreadyProcessed:
        return JSONResponse(
            {"ok": True, "detail": "already_processed"}, status_code=200,
        )
    except PaymentNotApproved:
        return JSONResponse({"ok": True}, status_code=200)
    except ValueError as exc:
        logger.warning("PayPal webhook: %s", exc)
        return JSONResponse({"ok": True}, status_code=200)
    except Exception:
        logger.exception("PayPal webhook: error for order %s", order_id)
        db.rollback()
        return JSONResponse({"ok": True}, status_code=200)


# ── Back-URL de redirección ──────────────────────────────────────────

@router.get("/result")
async def payment_result(status: str = "ok") -> JSONResponse:
    """Back-URL de redirección después del pago."""
    return JSONResponse({"status": status, "message": "Vuelve al bot de Telegram."})
