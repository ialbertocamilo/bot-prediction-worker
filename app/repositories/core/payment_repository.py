from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.payment import Payment


class PaymentRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def exists(self, mp_payment_id: str) -> bool:
        stmt = select(Payment.id).where(Payment.mp_payment_id == mp_payment_id)
        return self.db.scalar(stmt) is not None

    def create(
        self,
        mp_payment_id: str,
        telegram_id: int,
        amount: float,
        credits_granted: int,
        status: str,
    ) -> Payment:
        payment = Payment(
            mp_payment_id=mp_payment_id,
            telegram_id=telegram_id,
            amount=amount,
            credits_granted=credits_granted,
            status=status,
        )
        self.db.add(payment)
        self.db.flush()
        self.db.refresh(payment)
        return payment
