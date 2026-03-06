from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.season import Season


class SeasonRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, season_id: int) -> Season | None:
        return self.db.get(Season, season_id)

    def find_by_league_and_year(
        self,
        league_id: int,
        year: int,
    ) -> Season | None:
        stmt = (
            select(Season)
            .where(Season.league_id == league_id)
            .where(Season.year == year)
        )
        return self.db.scalar(stmt)

    def create(
        self,
        league_id: int,
        year: int,
        start_date: date | None = None,
        end_date: date | None = None,
        is_current: bool | None = None,
    ) -> Season:
        season: Season = Season(
            league_id=league_id,
            year=year,
            start_date=start_date,
            end_date=end_date,
            is_current=is_current,
        )
        self.db.add(season)
        self.db.flush()
        self.db.refresh(season)
        return season

    def get_or_create(
        self,
        league_id: int,
        year: int,
        start_date: date | None = None,
        end_date: date | None = None,
        is_current: bool | None = None,
    ) -> Season:
        season: Season | None = self.find_by_league_and_year(
            league_id=league_id,
            year=year,
        )
        if season is not None:
            return season

        return self.create(
            league_id=league_id,
            year=year,
            start_date=start_date,
            end_date=end_date,
            is_current=is_current,
        )