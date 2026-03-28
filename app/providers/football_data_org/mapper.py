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

SOURCE_NAME = "football-data-org"


class FootballDataOrgMapper:
    """Convierte respuestas de football-data.org v4 → modelos canónicos."""

    # ── Status ──────────────────────────────────────────────────

    @staticmethod
    def _map_status(raw_status: str | None) -> MatchStatus:
        mapping: dict[str, MatchStatus] = {
            "SCHEDULED": MatchStatus.scheduled,
            "TIMED": MatchStatus.scheduled,
            "IN_PLAY": MatchStatus.in_play,
            "PAUSED": MatchStatus.in_play,
            "FINISHED": MatchStatus.finished,
            "POSTPONED": MatchStatus.postponed,
            "CANCELLED": MatchStatus.cancelled,
            "SUSPENDED": MatchStatus.unknown,
            "AWARDED": MatchStatus.finished,
        }
        return mapping.get(raw_status or "", MatchStatus.unknown)

    # ── Match ───────────────────────────────────────────────────

    @staticmethod
    def map_match(raw: dict[str, Any]) -> CanonicalMatch:
        home: dict[str, Any] = raw.get("homeTeam") or {}
        away: dict[str, Any] = raw.get("awayTeam") or {}
        score: dict[str, Any] = raw.get("score") or {}
        full_time: dict[str, Any] = score.get("fullTime") or {}
        half_time: dict[str, Any] = score.get("halfTime") or {}
        competition: dict[str, Any] = raw.get("competition") or {}
        season: dict[str, Any] = raw.get("season") or {}
        referees: list[dict[str, Any]] = raw.get("referees") or []

        utc_date = datetime.fromisoformat(
            raw["utcDate"].replace("Z", "+00:00")
        )

        # Season year: usar startDate si existe, sino caer al campo season
        season_year: int | None = None
        start_date: str | None = season.get("startDate")
        if start_date:
            season_year = int(start_date[:4])

        referee_name: str | None = None
        for ref in referees:
            if ref.get("type") == "REFEREE":
                referee_name = ref.get("name")
                break

        matchday = raw.get("matchday")
        round_str = f"Matchday {matchday}" if matchday else None

        return CanonicalMatch(
            league_name=competition.get("name"),
            season_year=season_year,
            utc_date=utc_date,
            status=FootballDataOrgMapper._map_status(raw.get("status")),
            home_team_name=home.get("name", "Unknown"),
            away_team_name=away.get("name", "Unknown"),
            home_team_external_id=str(home["id"]) if home.get("id") else None,
            away_team_external_id=str(away["id"]) if away.get("id") else None,
            home_team_crest_url=home.get("crest"),
            away_team_crest_url=away.get("crest"),
            home_goals=full_time.get("home"),
            away_goals=full_time.get("away"),
            ht_home_goals=half_time.get("home"),
            ht_away_goals=half_time.get("away"),
            round=round_str,
            referee=referee_name,
            source_ref=CanonicalSourceRef(
                source_name=SOURCE_NAME,
                entity_type="match",
                external_id=str(raw["id"]),
            ),
        )

    # ── Team ────────────────────────────────────────────────────

    @staticmethod
    def map_team(raw: dict[str, Any]) -> CanonicalTeam:
        return CanonicalTeam(
            name=raw["name"],
            short_name=raw.get("tla"),
            country=raw.get("area", {}).get("name"),
            founded_year=raw.get("founded"),
            crest_url=raw.get("crest"),
        )

    # ── League / Competition ────────────────────────────────────

    @staticmethod
    def map_league(raw: dict[str, Any]) -> CanonicalLeague | None:
        name: str | None = raw.get("name")
        if not name:
            return None
        area: dict[str, Any] = raw.get("area") or {}
        return CanonicalLeague(
            name=name,
            country=area.get("name"),
            level=None,
        )

    # ── Events (limitado en plan gratuito) ──────────────────────

    @staticmethod
    def map_event(raw: dict[str, Any], match_external_id: str) -> CanonicalMatchEvent:
        """Mapea un goal del array 'goals' de un match detail."""
        scorer: dict[str, Any] = raw.get("scorer") or {}
        assist: dict[str, Any] = raw.get("assist") or {}

        return CanonicalMatchEvent(
            match_external_id=match_external_id,
            extra_minute=None,
            team_name=None,
            team_external_id=None,
            player_name=scorer.get("name"),
            assist_name=assist.get("name"),
            event_type=EventType.goal,
            event_detail=raw.get("type"),
            source_ref=CanonicalSourceRef(
                source_name=SOURCE_NAME,
                entity_type="event",
                external_id=f"{match_external_id}-{raw.get('minute', 0)}",
            ),
        )
