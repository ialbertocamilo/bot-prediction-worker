"""Payment webhooks — multi-gateway (raw Request, anti-422 design)."""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models.core.payment import Payment
from app.db.models.core.user import User
from app.services.payments.mercadopago_provider import MercadoPagoProvider
from app.services.payments.paypal_provider import PayPalProvider

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Mercado Pago webhook ─────────────────────────────────────────────


@router.post("/webhook/mercadopago")
async def mp_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Recibe notificaciones de Mercado Pago (Webhooks v2).

    Parsea el body crudo para evitar errores 422 por esquema Pydantic.
    """
    body = await request.body()
    if body:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    else:
        payload = {}
    print(f"🔔 MP WEBHOOK RECIBIDO: {payload}")

    payment_id = payload.get("data", {}).get("id") or payload.get("id") or request.query_params.get("id")
    if not payment_id:
        print("⚠️ MP WEBHOOK: No se encontró payment_id — ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    payment_id = str(payment_id)
    print(f"🔍 MP WEBHOOK: payment_id extraído = {payment_id}")

    pago_existente = db.query(Payment).filter(Payment.mp_payment_id == payment_id).first()
    if pago_existente:
        print(f"⏳ [AUDITORÍA] Pago MP {payment_id} ya procesado. Ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        provider = MercadoPagoProvider()
        result = await provider.verify_payment(payment_id)

        if result.status != "approved":
            print(f"⏳ MP WEBHOOK: Pago {payment_id} no aprobado (status={result.status}) — ignorando.")
            return JSONResponse({"status": "ok"}, status_code=200)

        external_reference = result.external_reference
        if not external_reference:
            print(f"⚠️ MP WEBHOOK: Pago {payment_id} aprobado pero sin external_reference.")
            return JSONResponse({"status": "ok"}, status_code=200)

        telegram_id = int(external_reference)
        print(f"👤 MP WEBHOOK: telegram_id extraído = {telegram_id}")

    except Exception as e:
        print(f"❌ MP WEBHOOK: Error verificando pago {payment_id}:\n{traceback.format_exc()}")
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        user = db.query(User).with_for_update().filter(User.telegram_id == telegram_id).first()
        if not user:
            print(f"⚠️ MP WEBHOOK: Usuario telegram_id={telegram_id} no encontrado en BD.")
            return JSONResponse({"status": "ok"}, status_code=200)

        user.creditos += 50
        mp_currency = os.getenv("MP_ITEM_CURRENCY", "PEN")
        db.add(Payment(
            mp_payment_id=payment_id,
            telegram_id=telegram_id,
            amount=result.amount,
            credits_granted=50,
            status="approved",
            currency=mp_currency,
        ))
        db.commit()
        print(f"✅ ÉXITO MP: 50 CRÉDITOS SUMADOS A {telegram_id} — Saldo actual: {user.creditos}")

    except Exception as e:
        db.rollback()
        print(f"❌ MP WEBHOOK: Error en persistencia para {telegram_id}:\n{traceback.format_exc()}")

    return JSONResponse({"status": "ok"}, status_code=200)


# ── Legacy alias: keep /webhook working during migration ─────────────

@router.post("/webhook")
async def mp_webhook_legacy(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Alias for ``/webhook/mercadopago`` — backwards-compatible."""
    return await mp_webhook(request, db)


# ── PayPal webhook ────────────────────────────────────────────────────


@router.post("/webhook/paypal")
async def paypal_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Recibe notificaciones webhook de PayPal.

    Escucha CHECKOUT.ORDER.APPROVED, captura fondos y suma créditos.
    """
    body = await request.body()
    if body:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    else:
        payload = {}
    print(f"🔔 PAYPAL WEBHOOK RECIBIDO: {payload}")

    if payload.get("event_type") != "CHECKOUT.ORDER.APPROVED":
        print(f"⏳ PAYPAL WEBHOOK: event_type={payload.get('event_type')} — ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    # Extraer telegram_id desde reference_id de purchase_units
    reference_id = payload.get("resource", {}).get("purchase_units", [{}])[0].get("reference_id")
    if not reference_id:
        print("⚠️ PAYPAL WEBHOOK: No se encontró reference_id en purchase_units — ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        telegram_id = int(reference_id)
    except (ValueError, TypeError):
        print(f"⚠️ PAYPAL WEBHOOK: reference_id no es un entero válido: {reference_id}")
        return JSONResponse({"status": "ok"}, status_code=200)

    print(f"👤 PAYPAL WEBHOOK: telegram_id extraído = {telegram_id}")

    order_id = payload.get("resource", {}).get("id")
    if not order_id:
        print("⚠️ PAYPAL WEBHOOK: No se encontró order_id en resource — ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    pago_existente = db.query(Payment).filter(Payment.mp_payment_id == str(order_id)).first()
    if pago_existente:
        print(f"⏳ [AUDITORÍA] Pago PayPal {order_id} ya procesado. Ignorando.")
        return JSONResponse({"status": "ok"}, status_code=200)

    # Capturar fondos de la orden
    try:
        provider = PayPalProvider()
        result = await provider.verify_payment(str(order_id))
        if result.status != "approved":
            print(f"⚠️ PAYPAL WEBHOOK: Captura de orden {order_id} no aprobada (status={result.status}).")
            return JSONResponse({"status": "ok"}, status_code=200)
        print(f"✅ PAYPAL WEBHOOK: Orden {order_id} capturada exitosamente.")
    except Exception as e:
        print(f"❌ PAYPAL WEBHOOK: Error capturando orden {order_id}:\n{traceback.format_exc()}")
        return JSONResponse({"status": "ok"}, status_code=200)

    try:
        user = db.query(User).with_for_update().filter(User.telegram_id == telegram_id).first()
        if not user:
            print(f"⚠️ PAYPAL WEBHOOK: Usuario telegram_id={telegram_id} no encontrado en BD.")
            return JSONResponse({"status": "ok"}, status_code=200)

        user.creditos += 50
        paypal_amount = float(
            payload.get("resource", {}).get("purchase_units", [{}])[0]
            .get("amount", {}).get("value", "0")
        )
        paypal_currency = (
            payload.get("resource", {}).get("purchase_units", [{}])[0]
            .get("amount", {}).get("currency_code", "USD")
        )
        db.add(Payment(
            mp_payment_id=str(order_id),
            telegram_id=telegram_id,
            amount=paypal_amount,
            credits_granted=50,
            status="approved",
            currency=paypal_currency,
        ))
        db.commit()
        print(f"💰 [PAYPAL] ORDEN {order_id} CAPTURADA Y CRÉDITOS SUMADOS A {telegram_id}")

    except Exception as e:
        db.rollback()
        print(f"❌ PAYPAL WEBHOOK: Error en persistencia para {telegram_id}:\n{traceback.format_exc()}")

    return JSONResponse({"status": "ok"}, status_code=200)


# ── Back-URL de redirección ──────────────────────────────────────────

@router.get("/result")
async def payment_result(status: str = "ok") -> JSONResponse:
    """Back-URL de redirección después del pago."""
    return JSONResponse({"status": status, "message": "Vuelve al bot de Telegram."})
