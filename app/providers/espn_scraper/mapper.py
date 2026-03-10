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

SOURCE_NAME = "espn-scraper"


class EspnScraperMapper:
    """Convierte respuestas de ESPN Soccer API → modelos canónicos."""

    # ── Status ──────────────────────────────────────────────────

    @staticmethod
    def _map_status(status_data: dict[str, Any]) -> MatchStatus:
        status_type: dict[str, Any] = status_data.get("type", {})
        state: str = status_type.get("state", "")
        name: str = status_type.get("name", "")

        if state == "pre" or name == "STATUS_SCHEDULED":
            return MatchStatus.scheduled
        if state == "in" or name in ("STATUS_IN_PROGRESS", "STATUS_HALFTIME"):
            return MatchStatus.in_play
        if state == "post" or name == "STATUS_FINAL":
            return MatchStatus.finished
        if name == "STATUS_POSTPONED":
            return MatchStatus.postponed
        if name == "STATUS_CANCELED":
            return MatchStatus.cancelled
        return MatchStatus.unknown

    # ── Match (desde un evento ESPN) ────────────────────────────

    @staticmethod
    def map_match(event: dict[str, Any]) -> CanonicalMatch:
        competitions: list[dict[str, Any]] = event.get("competitions", [])
        comp: dict[str, Any] = competitions[0] if competitions else {}

        competitors: list[dict[str, Any]] = comp.get("competitors", [])
        home: dict[str, Any] = {}
        away: dict[str, Any] = {}
        for c in competitors:
            if c.get("homeAway") == "home":
                home = c
            elif c.get("homeAway") == "away":
                away = c

        home_team: dict[str, Any] = home.get("team", {})
        away_team: dict[str, Any] = away.get("team", {})

        status_data: dict[str, Any] = comp.get("status", {})
        status: MatchStatus = EspnScraperMapper._map_status(status_data)

        # Goles: el campo "score" es un string en ESPN
        home_goals: int | None = None
        away_goals: int | None = None
        if status == MatchStatus.finished:
            try:
                home_goals = int(home.get("score", "0"))
            except (ValueError, TypeError):
                pass
            try:
                away_goals = int(away.get("score", "0"))
            except (ValueError, TypeError):
                pass

        # Fecha UTC
        utc_date = datetime.fromisoformat(
            event["date"].replace("Z", "+00:00")
        )

        # Season
        season_data: dict[str, Any] = event.get("season", {})
        season_year: int | None = season_data.get("year")

        # League name desde el nombre del evento o el season slug
        league_name: str | None = None
        # Intentar extraer de la competición
        for lg in event.get("leagues", []):
            league_name = lg.get("name")
            break
        # ESPN no siempre incluye leagues en el evento, pero sí a nivel raíz

        return CanonicalMatch(
            league_name=league_name,
            season_year=season_year,
            utc_date=utc_date,
            status=status,
            home_team_name=home_team.get("displayName", home_team.get("name", "Unknown")),
            away_team_name=away_team.get("displayName", away_team.get("name", "Unknown")),
            home_team_external_id=home_team.get("id"),
            away_team_external_id=away_team.get("id"),
            home_goals=home_goals,
            away_goals=away_goals,
            ht_home_goals=None,  # ESPN no provee half-time en plan básico
            ht_away_goals=None,
            round=None,
            referee=None,
            source_ref=CanonicalSourceRef(
                source_name=SOURCE_NAME,
                entity_type="match",
                external_id=str(event["id"]),
            ),
        )

    # ── Team (desde standings) ──────────────────────────────────

    @staticmethod
    def map_team(team_entry: dict[str, Any]) -> CanonicalTeam:
        """Mapea un equipo del endpoint de standings."""
        team: dict[str, Any] = team_entry.get("team", team_entry)
        return CanonicalTeam(
            name=team.get("displayName", team.get("name", "Unknown")),
            short_name=team.get("abbreviation"),
            country=None,  # ESPN no provee país directamente en standings
            founded_year=None,
        )

    # ── League ──────────────────────────────────────────────────

    @staticmethod
    def map_league(league_data: dict[str, Any]) -> CanonicalLeague | None:
        name: str | None = league_data.get("name")
        if not name:
            return None
        return CanonicalLeague(
            name=name,
            country=None,
            level=None,
        )

    # ── Events (goles parcial) ──────────────────────────────────

    @staticmethod
    def map_events_from_match(
        event: dict[str, Any],
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        """Extrae eventos (goles) de los detalles de un partido ESPN."""
        competitions = event.get("competitions", [])
        if not competitions:
            return []

        details: list[dict[str, Any]] = competitions[0].get("details", [])
        events: list[CanonicalMatchEvent] = []

        for i, detail in enumerate(details):
            detail_type: str = detail.get("type", {}).get("text", "")
            athlete: dict[str, Any] = (detail.get("athletesInvolved") or [{}])[0] if detail.get("athletesInvolved") else {}
            clock_str: str = detail.get("clock", {}).get("displayValue", "")
            minute: int | None = None
            if clock_str:
                try:
                    minute = int(clock_str.split("'")[0].strip().rstrip("'"))
                except (ValueError, IndexError):
                    pass

            event_type = EventType.other
            if "goal" in detail_type.lower():
                event_type = EventType.goal
            elif "card" in detail_type.lower():
                event_type = EventType.card
            elif "sub" in detail_type.lower():
                event_type = EventType.substitution

            events.append(
                CanonicalMatchEvent(
                    match_external_id=match_external_id,
                    extra_minute=minute,
                    team_name=detail.get("team", {}).get("displayName"),
                    team_external_id=detail.get("team", {}).get("id"),
                    player_name=athlete.get("displayName"),
                    assist_name=None,
                    event_type=event_type,
                    event_detail=detail_type,
                    source_ref=CanonicalSourceRef(
                        source_name=SOURCE_NAME,
                        entity_type="event",
                        external_id=f"{match_external_id}-{i}",
                    ),
                )
            )
        return events
