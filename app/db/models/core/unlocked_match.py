from sqlalchemy import BigInteger, Integer, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UnlockedMatch(Base):
    """Tracks which matches a user has already paid to predict.

    Once a user spends 1 credit on a match_id, subsequent predictions
    for that same match are free (entitlement / "match pass").
    """

    __tablename__ = "user_unlocked_matches"
    __table_args__ = (
        UniqueConstraint("telegram_id", "match_id", name="uq_user_match_unlock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )

    match_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    unlocked_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
