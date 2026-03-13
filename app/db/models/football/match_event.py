from sqlalchemy import String, Integer, DateTime, ForeignKey, func, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MatchEvent(Base):
    __tablename__ = "match_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False
    )

    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True
    )

    player_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    assist_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # GOAL/CARD/...
    event_detail: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    match = relationship("Match", lazy="joined")
    team = relationship("Team", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "match_id", "minute", "team_id", "event_type", "player_name",
            name="uq_match_events_dedup",
        ),
        Index("ix_match_events_match_minute", "match_id", "minute"),
    )