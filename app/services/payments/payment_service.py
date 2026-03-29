"""PaymentService — orchestrates provider ↔ DB ↔ credits."""
from __future__ import annotations

import logging
import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.core.payment_repository import PaymentRepository
from app.repositories.core.user_repository import UserRepository
from app.services.payments.base import (
    GatewayPricing,
    PaymentItem,
    PaymentProvider,
    PaymentResult,
)

logger = logging.getLogger(__name__)

CREDITS_PER_PURCHASE: int = int(os.getenv("MP_CREDITS_PER_PURCHASE", "50"))
ITEM_PRICE: float = float(os.getenv("MP_ITEM_PRICE", "50.00"))
ITEM_CURRENCY: str = os.getenv("MP_ITEM_CURRENCY", "PEN")


class PaymentService:
    """Gateway-agnostic payment orchestrator.

    Receives a ``PaymentProvider`` (Strategy) so the same DB / credit
    logic works for Mercado Pago, PayPal, or any future gateway.
    """

    def __init__(self, db: Session, provider: PaymentProvider) -> None:
        self._db = db
        self._provider = provider
        self._user_repo = UserRepository(db)
        self._payment_repo = PaymentRepository(db)

    @property
    def pricing(self) -> GatewayPricing:
        return self._provider.pricing

    async def create_checkout(self, telegram_id: int) -> str:
        """Build a checkout preference and return the payment URL."""
        self._user_repo.get_or_create(telegram_id=telegram_id)

        gw_pricing: GatewayPricing = self._provider.pricing

        items: list[PaymentItem] = [
            PaymentItem(
                title=f"FútbolQuant — {CREDITS_PER_PURCHASE} créditos",
                quantity=1,
                unit_price=gw_pricing.price,
                currency_id=gw_pricing.currency,
            ),
        ]

        init_point: str = await self._provider.create_preference(
            items=items,
            external_reference=str(telegram_id),
        )
        logger.info(
            "Checkout created for tg_id=%d via %s (%s %s)",
            telegram_id,
            type(self._provider).__name__,
            gw_pricing.price,
            gw_pricing.currency,
        )
        return init_point

    async def process_approved_payment(
        self,
        payment_id: str,
        *,
        simulated: PaymentResult | None = None,
    ) -> int:
        """Verify + accredit credits for an approved payment.

        Security layers:
          1. Fast idempotency check (optimistic, no lock).
          2. Verify payment status via gateway SDK.
          3. ``SELECT … FOR UPDATE`` on user row → serialises concurrent
             webhooks for the same telegram_id.
          4. Re-check idempotency under lock (prevents double-spend race).
          5. Atomic ``UPDATE … SET creditos = creditos + N``.
          6. INSERT payment record — ``UNIQUE(mp_payment_id)`` as final
             guard, ``IntegrityError`` caught to avoid 500.

        Returns:
            New credit balance after accreditation.

        Raises:
            ``PaymentAlreadyProcessed`` — idempotency (any layer).
            ``PaymentNotApproved`` — status != approved.
            ``ValueError`` — invalid external_reference.
        """
        # ── 1. Fast idempotency (optimistic, no lock) ────────────
        if self._payment_repo.exists(payment_id):
            logger.info("Payment %s already processed (fast-path)", payment_id)
            raise PaymentAlreadyProcessed(payment_id)

        # ── 2. Verify with gateway or use simulated data ─────────
        result: PaymentResult = (
            simulated or await self._provider.verify_payment(payment_id)
        )

        if result.status != "approved":
            logger.info(
                "Payment %s status=%s (not approved)", payment_id, result.status,
            )
            raise PaymentNotApproved(payment_id, result.status)

        external_ref: str = result.external_reference
        if not external_ref.isdigit():
            raise ValueError(f"Invalid external_reference: {external_ref!r}")

        telegram_id: int = int(external_ref)

        # ── 3. Ensure user exists ────────────────────────────────
        self._user_repo.get_or_create(telegram_id=telegram_id)

        # ── 4. Lock user row (SELECT … FOR UPDATE) ──────────────
        locked_user = self._user_repo.get_for_update(telegram_id)
        if locked_user is None:
            raise ValueError(
                f"User telegram_id={telegram_id} not found after get_or_create"
            )

        # ── 5. Re-check idempotency under lock ──────────────────
        if self._payment_repo.exists(payment_id):
            logger.info("Payment %s already processed (under lock)", payment_id)
            raise PaymentAlreadyProcessed(payment_id)

        # ── 6. Accredit credits (row is locked) ─────────────────
        new_balance: int = self._user_repo.add_creditos(
            telegram_id, CREDITS_PER_PURCHASE,
        )

        # ── 7. Record payment (UNIQUE constraint = final guard) ──
        try:
            self._payment_repo.create(
                mp_payment_id=payment_id,
                telegram_id=telegram_id,
                amount=result.amount,
                credits_granted=CREDITS_PER_PURCHASE,
                status=result.status,
            )
        except IntegrityError:
            self._db.rollback()
            logger.warning(
                "Payment %s duplicate INSERT caught by UNIQUE constraint",
                payment_id,
            )
            raise PaymentAlreadyProcessed(payment_id) from None

        logger.info(
            "Payment %s → tg_id=%d +%d créditos (balance=%d) via %s",
            payment_id,
            telegram_id,
            CREDITS_PER_PURCHASE,
            new_balance,
            type(self._provider).__name__,
        )
        return new_balance


# ── Domain exceptions ─────────────────────────────────────────────────


class PaymentAlreadyProcessed(Exception):
    """Raised when a payment was already accredited (idempotency)."""

    def __init__(self, payment_id: str) -> None:
        self.payment_id = payment_id
        super().__init__(f"Payment {payment_id} already processed")


class PaymentNotApproved(Exception):
    """Raised when a payment status ≠ approved."""

    def __init__(self, payment_id: str, status: str) -> None:
        self.payment_id = payment_id
        self.status = status
        super().__init__(f"Payment {payment_id} status={status}")
