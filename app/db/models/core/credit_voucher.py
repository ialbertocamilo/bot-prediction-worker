from sqlalchemy import BigInteger, Boolean, Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CreditVoucher(Base):
    __tablename__ = "credit_vouchers"

    id: Mapped[int] = mapped_column(primary_key=True)

    code: Mapped[str] = mapped_column(
        String(19),
        unique=True,
        nullable=False,
        index=True,
    )

    credits: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    is_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    used_by: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    redeemed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
