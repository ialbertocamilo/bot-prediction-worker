"""VoucherService — generate and redeem credit vouchers."""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.credit_voucher import CreditVoucher
from app.repositories.core.user_repository import UserRepository

logger = logging.getLogger(__name__)

_ALPHABET = string.ascii_uppercase + string.digits


def generate_voucher(db: Session, credits: int) -> str:
    """Create a new voucher with the given credit amount.

    Returns the formatted code ``XXXX-XXXX-XXXX-XXXX``.
    """
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))

    voucher = CreditVoucher(code=code, credits=credits)
    db.add(voucher)
    db.commit()

    logger.info("Voucher created: %s (%d credits)", code, credits)
    return code


def redeem_voucher(db: Session, telegram_id: int, code: str) -> tuple[int, int]:
    """Redeem a voucher and credit the user.

    Returns a tuple ``(credits_granted, new_balance)``.

    Raises:
        ValueError: if the voucher does not exist or was already used.
    """
    try:
        # 1. Lock the voucher row
        stmt = (
            select(CreditVoucher)
            .where(CreditVoucher.code == code)
            .with_for_update()
        )
        voucher: CreditVoucher | None = db.scalar(stmt)

        if voucher is None:
            raise ValueError(f"Voucher '{code}' no existe.")
        if voucher.is_used:
            raise ValueError(f"Voucher '{code}' ya fue canjeado.")

        # 2. Mark voucher as used
        voucher.is_used = True
        voucher.used_by = telegram_id
        voucher.redeemed_at = datetime.now(timezone.utc)

        # 3. Lock user row and add credits
        user_repo = UserRepository(db)
        user_repo.get_or_create(telegram_id=telegram_id)
        user_repo.get_for_update(telegram_id=telegram_id)
        new_balance: int = user_repo.add_creditos(telegram_id, voucher.credits)

        # 4. Single commit
        db.commit()

        logger.info(
            "Voucher %s redeemed by tg_id=%d (+%d credits, balance=%d)",
            code, telegram_id, voucher.credits, new_balance,
        )
        return voucher.credits, new_balance

    except Exception:
        db.rollback()
        raise
