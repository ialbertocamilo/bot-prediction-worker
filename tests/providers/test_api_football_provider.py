from __future__ import annotations

from datetime import date

from app.providers.api_football.provider import ApiFootballProvider
from tests.providers.fakes.fake_api_football_client import FakeApiFootballClient


def test_api_football_provider_maps_fixtures() -> None:
    provider = ApiFootballProvider(client=FakeApiFootballClient())

    matches = provider.get_fixtures(
        league_id=281,
        season=2026,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 7),
    )

    assert len(matches) == 1
    assert matches[0].home_team_name == "Universitario"
    assert matches[0].away_team_name == "Alianza Lima"
    assert matches[0].source_ref is not None
    assert matches[0].source_ref.external_id == "12345"


def test_api_football_provider_maps_teams() -> None:
    provider = ApiFootballProvider(client=FakeApiFootballClient())

    teams = provider.get_teams(
        league_id=281,
        season=2026,
    )

    assert len(teams) == 1
    assert teams[0].name == "Universitario"


def test_api_football_provider_maps_league() -> None:
    provider = ApiFootballProvider(client=FakeApiFootballClient())

    league = provider.get_league(
        league_id=281,
        season=2026,
    )

    assert league is not None
    assert league.name == "Liga 1"
    assert league.country == "Peru"