from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.services.prediction.league_strength_service import LeagueStrengthService


def _match(
    *,
    home_key: str,
    away_key: str,
    home_goals: int,
    away_goals: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        status="FINISHED",
        home_goals=home_goals,
        away_goals=away_goals,
        home_team=SimpleNamespace(team_type="CLUB", domestic_league_key=home_key),
        away_team=SimpleNamespace(team_type="CLUB", domestic_league_key=away_key),
    )


class LeagueStrengthServiceTests(unittest.TestCase):
    def test_league_that_consistently_wins_gets_higher_coefficient(self) -> None:
        matches = [
            _match(home_key="premier-league", away_key="liga1-peru", home_goals=3, away_goals=0),
            _match(home_key="premier-league", away_key="liga1-peru", home_goals=2, away_goals=0),
            _match(home_key="premier-league", away_key="liga1-peru", home_goals=4, away_goals=1),
            _match(home_key="premier-league", away_key="liga1-peru", home_goals=3, away_goals=1),
        ]

        records, _ = LeagueStrengthService.compute_from_matches(matches)
        coeffs = {r.league_key: r.coefficient for r in records}

        self.assertGreater(coeffs["premier-league"], 1.0)
        self.assertLess(coeffs["liga1-peru"], 1.0)
        self.assertGreater(coeffs["premier-league"], coeffs["liga1-peru"])

    def test_small_sample_stays_close_to_one(self) -> None:
        matches = [
            _match(home_key="serie-a", away_key="mls", home_goals=1, away_goals=0),
        ]

        records, _ = LeagueStrengthService.compute_from_matches(matches)
        coeffs = {r.league_key: r.coefficient for r in records}

        self.assertLess(abs(coeffs["serie-a"] - 1.0), 0.1)
        self.assertLess(abs(coeffs["mls"] - 1.0), 0.1)

