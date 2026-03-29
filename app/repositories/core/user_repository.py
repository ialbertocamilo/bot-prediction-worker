from __future__ import annotations

from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import Session

from app.db.models.core.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = select(User).where(User.telegram_id == telegram_id)
        return self.db.scalar(stmt)

    def get_for_update(self, telegram_id: int) -> User | None:
        """Fetch user with a row-level lock (SELECT … FOR UPDATE).

        Prevents race conditions on concurrent credit checks.
        """
        stmt = (
            select(User)
            .where(User.telegram_id == telegram_id)
            .with_for_update()
        )
        return self.db.scalar(stmt)

    def get_or_create(self, telegram_id: int, username: str | None = None) -> User:
        user: User | None = self.get_by_telegram_id(telegram_id)
        if user is not None:
            return user
        user = User(telegram_id=telegram_id, username=username)
        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)
        return user

    def add_creditos(self, telegram_id: int, amount: int) -> int:
        """Atomically add credits and return the new balance."""
        stmt = (
            sa_update(User)
            .where(User.telegram_id == telegram_id)
            .values(creditos=User.creditos + amount)
            .returning(User.creditos)
        )
        new_balance: int | None = self.db.scalar(stmt)
        self.db.flush()
        if new_balance is None:
            raise ValueError(f"User with telegram_id={telegram_id} not found")
        return new_balance

    def deduct_credito(self, telegram_id: int, amount: int = 1) -> int:
        """Atomically subtract credits and return the new balance.

        Caller MUST hold a FOR UPDATE lock (via get_for_update) and
        have already validated that the balance is sufficient.
        """
        stmt = (
            sa_update(User)
            .where(User.telegram_id == telegram_id)
            .values(creditos=User.creditos - amount)
            .returning(User.creditos)
        )
        new_balance: int | None = self.db.scalar(stmt)
        self.db.flush()
        if new_balance is None:
            raise ValueError(f"User with telegram_id={telegram_id} not found")
        return new_balance

    def get_creditos(self, telegram_id: int) -> int:
        user = self.get_by_telegram_id(telegram_id)
        return user.creditos if user else 0
