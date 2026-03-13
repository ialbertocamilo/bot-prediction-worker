from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalMatchStats,
    CanonicalOdds,
    CanonicalPlayer,
    CanonicalTeam,
)


class BaseProvider(ABC):
    """Abstract base for all data providers (APIs and scrapers)."""

    @property
    def provider_name(self) -> str:
        """Unique identifier for this provider (e.g. 'espn-scraper')."""
        # Default: derive from class name. Override for custom names.
        return self.__class__.__name__

    @abstractmethod
    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        raise NotImplementedError

    @abstractmethod
    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalMatch]:
        raise NotImplementedError

    @abstractmethod
    def get_match_events(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchEvent]:
        raise NotImplementedError

    @abstractmethod
    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalTeam]:
        raise NotImplementedError

    @abstractmethod
    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> CanonicalLeague | None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Optional methods — override in providers that support these.
    # ------------------------------------------------------------------

    def get_players(
        self,
        league_id: int,
        season: int,
    ) -> list[CanonicalPlayer]:
        return []

    def get_match_stats(
        self,
        match_external_id: str,
    ) -> list[CanonicalMatchStats]:
        return []

    def get_finished_events_page(
        self,
        page: int = 0,
    ) -> list[dict]:
        """Return a page of finished events for iterating stats coverage.

        Each dict should have at minimum:
            {"id": str, "home_team": str, "away_team": str}

        Providers that don't support event listing return [].
        """
        return []

    def get_odds(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> list[CanonicalOdds]:
        """Return 1X2 market odds for matches in the date range.

        Providers that don't support odds return [].
        """
        return []