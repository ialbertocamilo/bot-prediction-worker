from __future__ import annotations

from datetime import date
from typing import Any


class FakeApiFootballClient:
    def get_fixtures(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        return {
            "response": [
                {
                    "fixture": {
                        "id": 12345,
                        "date": "2026-03-20T20:00:00+00:00",
                        "referee": "Ref Test",
                        "status": {"short": "NS"},
                    },
                    "league": {
                        "name": "Liga 1",
                        "season": 2026,
                        "round": "Regular Season - 1",
                    },
                    "teams": {
                        "home": {"id": 44, "name": "Universitario"},
                        "away": {"id": 50, "name": "Alianza Lima"},
                    },
                    "goals": {
                        "home": None,
                        "away": None,
                    },
                    "score": {
                        "halftime": {
                            "home": None,
                            "away": None,
                        }
                    },
                }
            ]
        }

    def get_results(
        self,
        league_id: int,
        season: int,
        date_from: date,
        date_to: date,
    ) -> dict[str, Any]:
        return {"response": []}

    def get_match_events(
        self,
        match_external_id: str,
    ) -> dict[str, Any]:
        return {"response": []}

    def get_teams(
        self,
        league_id: int,
        season: int,
    ) -> dict[str, Any]:
        return {
            "response": [
                {
                    "team": {
                        "id": 44,
                        "name": "Universitario",
                        "code": "UNI",
                        "country": "Peru",
                        "founded": 1924,
                    }
                }
            ]
        }

    def get_league(
        self,
        league_id: int,
        season: int,
    ) -> dict[str, Any]:
        return {
            "response": [
                {
                    "league": {
                        "id": league_id,
                        "name": "Liga 1",
                    },
                    "country": {
                        "name": "Peru",
                    },
                }
            ]
        }