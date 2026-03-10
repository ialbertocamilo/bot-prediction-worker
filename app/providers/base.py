from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.domain.canonical import (
    CanonicalLeague,
    CanonicalMatch,
    CanonicalMatchEvent,
    CanonicalMatchStats,
    CanonicalPlayer,
    CanonicalTeam,
)


class BaseProvider(ABC):
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