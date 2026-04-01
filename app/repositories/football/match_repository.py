from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match

_SENTINEL = object()  # distinguish "not passed" from None


class MatchRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, match_id: int) -> Match | None:
        return self.db.get(Match, match_id)

    def find_by_signature(
        self,
        league_id: int,
        utc_date: datetime,
        home_team_id: int,
        away_team_id: int,
    ) -> Match | None:
        stmt = (
            select(Match)
            .where(Match.league_id == league_id)
            .where(Match.utc_date == utc_date)
            .where(Match.home_team_id == home_team_id)
            .where(Match.away_team_id == away_team_id)
        )
        return self.db.scalar(stmt)

    def create(
        self,
        league_id: int,
        utc_date: datetime,
        status: str,
        home_team_id: int,
        away_team_id: int,
        season_id: int | None = None,
        venue_id: int | None = None,
        home_goals: int | None = None,
        away_goals: int | None = None,
        ht_home_goals: int | None = None,
        ht_away_goals: int | None = None,
        round_value: str | None = None,
        referee: str | None = None,
        clock_display: str | None = None,
    ) -> Match:
        match: Match = Match(
            league_id=league_id,
            season_id=season_id,
            venue_id=venue_id,
            utc_date=utc_date,
            status=status,
            is_finished=status == "FINISHED",
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_goals=home_goals,
            away_goals=away_goals,
            ht_home_goals=ht_home_goals,
            ht_away_goals=ht_away_goals,
            round=round_value,
            referee=referee,
            clock_display=clock_display,
        )
        self.db.add(match)
        self.db.flush()
        self.db.refresh(match)
        return match

    def update(
        self,
        match: Match,
        *,
        status: str | None = None,
        venue_id: int | None = None,
        home_team_id: int | None = None,
        away_team_id: int | None = None,
        home_goals: int | None = None,
        away_goals: int | None = None,
        ht_home_goals: int | None = None,
        ht_away_goals: int | None = None,
        round_value: str | None = None,
        referee: str | None = None,
        clock_display: str | None = _SENTINEL,
    ) -> Match:
        if status is not None:
            match.status = status
            match.is_finished = status == "FINISHED"
        if venue_id is not None:
            match.venue_id = venue_id

        if home_team_id is not None:
            match.home_team_id = home_team_id
        if away_team_id is not None:
            match.away_team_id = away_team_id

        if home_goals is not None:
            match.home_goals = home_goals
        if away_goals is not None:
            match.away_goals = away_goals
        if ht_home_goals is not None:
            match.ht_home_goals = ht_home_goals
        if ht_away_goals is not None:
            match.ht_away_goals = ht_away_goals

        if round_value is not None:
            match.round = round_value
        if referee is not None:
            match.referee = referee

        if clock_display is not _SENTINEL:
            match.clock_display = clock_display

        self.db.flush()
        self.db.refresh(match)
        return match

    def get_or_create(
        self,
        league_id: int,
        utc_date: datetime,
        status: str,
        home_team_id: int,
        away_team_id: int,
        season_id: int | None = None,
        venue_id: int | None = None,
        home_goals: int | None = None,
        away_goals: int | None = None,
        ht_home_goals: int | None = None,
        ht_away_goals: int | None = None,
        round_value: str | None = None,
        referee: str | None = None,
        clock_display: str | None = None,
    ) -> Match:
        match: Match | None = self.find_by_signature(
            league_id=league_id,
            utc_date=utc_date,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
        if match is not None:
            return match

        return self.create(
            league_id=league_id,
            utc_date=utc_date,
            status=status,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            season_id=season_id,
            venue_id=venue_id,
            home_goals=home_goals,
            away_goals=away_goals,
            ht_home_goals=ht_home_goals,
            ht_away_goals=ht_away_goals,
            round_value=round_value,
            referee=referee,
            clock_display=clock_display,
        )

    def list_upcoming_by_league(
        self,
        league_id: int,
    ) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.league_id == league_id)
            .where(Match.status == "SCHEDULED")
            .order_by(Match.utc_date.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_finished_by_league(
        self,
        league_id: int,
        limit: int | None = None,
    ) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.league_id == league_id)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def list_by_date_range(
        self,
        date_from: datetime,
        date_to: datetime,
        league_id: int | None = None,
    ) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.utc_date >= date_from)
            .where(Match.utc_date <= date_to)
            .order_by(Match.utc_date.asc())
        )
        if league_id is not None:
            stmt = stmt.where(Match.league_id == league_id)
        return list(self.db.scalars(stmt).all())

    def list_live(self) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.status.in_(["IN_PLAY", "PAUSED"]))
            .order_by(Match.utc_date.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_team(
        self,
        team_id: int,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Match]:
        stmt = (
            select(Match)
            .where(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id,
                )
            )
            .order_by(Match.utc_date.desc())
        )
        if status is not None:
            stmt = stmt.where(Match.status == status)
        stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def distinct_league_ids_for_team(
        self,
        team_id: int,
        cutoff: datetime | None = None,
    ) -> list[int]:
        stmt = (
            select(Match.league_id)
            .where(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id,
                )
            )
            .distinct()
        )
        if cutoff is not None:
            stmt = stmt.where(Match.utc_date >= cutoff)
        return list(self.db.scalars(stmt).all())