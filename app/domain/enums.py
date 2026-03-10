from __future__ import annotations

from enum import Enum


class SourceKind(str, Enum):
    api = "api"
    scraper = "scraper"
    manual = "manual"


class EntityType(str, Enum):
    league = "league"
    season = "season"
    team = "team"
    venue = "venue"
    match = "match"
    event = "event"
    odds = "odds"


class MatchStatus(str, Enum):
    scheduled = "SCHEDULED"
    in_play = "IN_PLAY"
    finished = "FINISHED"
    postponed = "POSTPONED"
    cancelled = "CANCELLED"
    unknown = "UNKNOWN"


class EventType(str, Enum):
    goal = "GOAL"
    card = "CARD"
    substitution = "SUBSTITUTION"
    var = "VAR"
    other = "OTHER"


class PlayerPosition(str, Enum):
    goalkeeper = "GOALKEEPER"
    defender = "DEFENDER"
    midfielder = "MIDFIELDER"
    forward = "FORWARD"
    unknown = "UNKNOWN"


class FootPreference(str, Enum):
    left = "LEFT"
    right = "RIGHT"
    both = "BOTH"
    unknown = "UNKNOWN"