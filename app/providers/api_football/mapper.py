from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalSourceRef,
    CanonicalTeam,
)
from app.domain.enums import EventType, MatchStatus


class ApiFootballMapper:
    @staticmethod
    def _map_status(raw_status: str | None) -> MatchStatus:
        if raw_status == "NS":
            return MatchStatus.scheduled
        if raw_status in {"1H", "2H", "HT", "ET", "BT", "LIVE"}:
            return MatchStatus.in_play
        if raw_status == "FT":
            return MatchStatus.finished
        if raw_status == "PST":
            return MatchStatus.postponed
        if raw_status == "CANC":
            return MatchStatus.cancelled
        return MatchStatus.unknown

    @staticmethod
    def _map_event_type(
        raw_event_type: str | None,
    ) -> EventType:
        normalized_value: str = (raw_event_type or "").strip().lower()

        if normalized_value == "goal":
            return EventType.goal
        if normalized_value == "card":
            return EventType.card
        if normalized_value == "subst":
            return EventType.substitution
        if normalized_value == "var":
            return EventType.var

        return EventType.other

    @staticmethod
    def map_match(raw_match: dict[str, Any]) -> CanonicalMatch:
        fixture: dict[str, Any] = raw_match["fixture"]
        league: dict[str, Any] = raw_match["league"]
        teams: dict[str, Any] = raw_match["teams"]
        goals: dict[str, Any] = raw_match["goals"]
        score: dict[str, Any] = raw_match.get("score", {})

        halftime: dict[str, Any] = score.get("halftime") or {}
        fixture_status: dict[str, Any] = fixture.get("status", {})

        utc_date: datetime = datetime.fromisoformat(
            fixture["date"].replace("Z", "+00:00")
        )

        mapped_status = ApiFootballMapper._map_status(fixture_status.get("short"))

        # Clock display para partidos en vivo (e.g. "45'" o "HT")
        clock_display: str | None = None
        if mapped_status == MatchStatus.in_play:
            elapsed = fixture_status.get("elapsed")
            short = fixture_status.get("short", "")
            if short == "HT":
                clock_display = "HT"
            elif elapsed is not None:
                clock_display = f"{elapsed}'"

        return CanonicalMatch(
            league_name=league.get("name"),
            season_year=league.get("season"),
            utc_date=utc_date,
            status=mapped_status,
            home_team_name=teams["home"]["name"],
            away_team_name=teams["away"]["name"],
            home_team_external_id=str(teams["home"]["id"]),
            away_team_external_id=str(teams["away"]["id"]),
            home_team_crest_url=teams["home"].get("logo"),
            away_team_crest_url=teams["away"].get("logo"),
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
            ht_home_goals=halftime.get("home"),
            ht_away_goals=halftime.get("away"),
            round=league.get("round"),
            referee=fixture.get("referee"),
            clock_display=clock_display,
            source_ref=CanonicalSourceRef(
                source_name="api-football",
                entity_type="match",
                external_id=str(fixture["id"]),
            ),
        )

    @staticmethod
    def map_event(raw_event: dict[str, Any]) -> CanonicalMatchEvent:
        team: dict[str, Any] = raw_event.get("team") or {}
        player: dict[str, Any] = raw_event.get("player") or {}
        assist: dict[str, Any] = raw_event.get("assist") or {}

        return CanonicalMatchEvent(
            match_external_id="",
            minute=raw_event.get("time", {}).get("elapsed"),
            extra_minute=raw_event.get("time", {}).get("extra"),
            team_name=team.get("name"),
            team_external_id=str(team["id"]) if team.get("id") is not None else None,
            player_name=player.get("name"),
            assist_name=assist.get("name"),
            event_type=ApiFootballMapper._map_event_type(raw_event.get("type")),
            event_detail=raw_event.get("detail"),
            source_ref=None,
        )

    @staticmethod
    def map_team(raw_team: dict[str, Any]) -> CanonicalTeam:
        team_data: dict[str, Any] = raw_team.get("team") or raw_team

        return CanonicalTeam(
            name=team_data["name"],
            short_name=team_data.get("code"),
            country=team_data.get("country"),
            founded_year=team_data.get("founded"),
            crest_url=team_data.get("logo"),
        )

    @staticmethod
    def map_league(raw_league: dict[str, Any]) -> CanonicalLeague | None:
        league_data: dict[str, Any] = raw_league.get("league") or {}
        country_data: dict[str, Any] = raw_league.get("country") or {}

        league_name: str | None = league_data.get("name")
        if league_name is None:
            return None

        return CanonicalLeague(
            name=league_name,
            country=country_data.get("name"),
            level=None,
        )