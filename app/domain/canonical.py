from __future__ import annotations

from datetime import datetime, date
from typing import Any, Optional, Literal

from pydantic import BaseModel, Field, ConfigDict

from app.domain.enums import MatchStatus, EventType


class CanonicalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalSourceRef(CanonicalBase):
    source_name: str = Field(min_length=1)              
    entity_type: str = Field(min_length=1)            
    external_id: str = Field(min_length=1)          
    fetched_at: datetime | None = None


class CanonicalLeague(CanonicalBase):
    name: str = Field(min_length=1)
    country: str | None = None
    level: int | None = None


class CanonicalSeason(CanonicalBase):
    league_name: str = Field(min_length=1)
    year: int
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None


class CanonicalTeam(CanonicalBase):
    name: str = Field(min_length=1)
    short_name: str | None = None
    country: str | None = None
    founded_year: int | None = None


class CanonicalVenue(CanonicalBase):
    name: str = Field(min_length=1)
    city: str | None = None
    capacity: int | None = None


class CanonicalMatch(CanonicalBase):
    league_name: str | None = None
    season_year: int | None = None

    utc_date: datetime
    status: MatchStatus = MatchStatus.unknown

    home_team_name: str = Field(min_length=1)
    away_team_name: str = Field(min_length=1)

    home_team_external_id: str | None = None
    away_team_external_id: str | None = None

    home_goals: int | None = None
    away_goals: int | None = None

    ht_home_goals: int | None = None
    ht_away_goals: int | None = None

    round: str | None = None
    referee: str | None = None

    source_ref: CanonicalSourceRef | None = None


class CanonicalMatchEvent(CanonicalBase):
    match_external_id: str = Field(min_length=1)  
    extra_minute: int | None = None

    team_name: str | None = None
    team_external_id: str | None = None

    player_name: str | None = None
    assist_name: str | None = None

    event_type: EventType = EventType.other
    event_detail: str | None = None

    source_ref: CanonicalSourceRef | None = None


class CanonicalOdds(CanonicalBase):
    match_external_id: str = Field(min_length=1)
    bookmaker: str | None = None
    market: str = Field(min_length=1)   
    selection: str = Field(min_length=1)  
    odd: float = Field(gt=1.0)
    collected_at: datetime

    source_ref: CanonicalSourceRef | None = None

class Prediction1X2(CanonicalBase):
    p_home: float = Field(ge=0.0, le=1.0)
    p_draw: float = Field(ge=0.0, le=1.0)
    p_away: float = Field(ge=0.0, le=1.0)

    model_version: str = Field(min_length=1)
    generated_at: datetime


class MatchPredictionResponse(CanonicalBase):
    match_id: int
    home_team: str
    away_team: str
    utc_date: datetime
    prediction: Prediction1X2
    notes: str | None = None