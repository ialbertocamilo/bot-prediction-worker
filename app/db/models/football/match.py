from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, func, Index, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)

    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="RESTRICT"),
        nullable=False
    )
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="SET NULL"),
        nullable=True
    )
    venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("venues.id", ondelete="SET NULL"),
        nullable=True
    )

    utc_date: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=False
    )

    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ht_home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)

    round: Mapped[str | None] = mapped_column(String(80), nullable=True)
    referee: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    league = relationship("League", lazy="joined")
    season = relationship("Season", lazy="joined")
    venue = relationship("Venue", lazy="joined")

    home_team = relationship("Team", foreign_keys=[home_team_id], lazy="joined")
    away_team = relationship("Team", foreign_keys=[away_team_id], lazy="joined")

    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="ck_matches_home_away_diff"),
        UniqueConstraint("league_id", "utc_date", "home_team_id", "away_team_id", name="uq_matches_signature"),

        Index("ix_matches_league_date", "league_id", "utc_date"),
        Index("ix_matches_season_date", "season_id", "utc_date"),
        Index("ix_matches_pair_date", "home_team_id", "away_team_id", "utc_date"),
    )