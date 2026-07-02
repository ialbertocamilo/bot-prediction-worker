from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.services.prediction.prediction_service import PredictionService
from app.services.prediction.schemas import MatchPredictionResult


def _make_match(
    *,
    match_id: int,
    league_name: str,
    home_name: str,
    away_name: str,
    home_type: str,
    away_type: str,
    home_key: str | None = None,
    away_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=match_id,
        league_id=77,
        league=SimpleNamespace(name=league_name),
        utc_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        status="SCHEDULED",
        home_team_id=10,
        away_team_id=20,
        home_team=SimpleNamespace(
            name=home_name,
            team_type=home_type,
            domestic_league_key=home_key,
            national_team_key=home_name.lower(),
        ),
        away_team=SimpleNamespace(
            name=away_name,
            team_type=away_type,
            domestic_league_key=away_key,
            national_team_key=away_name.lower(),
        ),
    )


def _make_result(match: SimpleNamespace) -> MatchPredictionResult:
    return MatchPredictionResult(
        match_id=match.id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        league=match.league.name,
        utc_date=match.utc_date,
        status=match.status,
        p_home=0.34,
        p_draw=0.29,
        p_away=0.37,
        p_over_1_5=0.72,
        p_under_1_5=0.28,
        p_over_2_5=0.49,
        p_under_2_5=0.51,
        p_over_3_5=0.25,
        p_under_3_5=0.75,
        p_btts_yes=0.52,
        p_btts_no=0.48,
        xg_home=1.33,
        xg_away=1.39,
        top_scorelines={"1-1": 0.12},
        model="dixon_coles_v1",
        data_quality="test",
    )


class PredictionServiceRoutingTests(unittest.TestCase):
    def test_predict_match_routes_national_teams_to_national_flow(self) -> None:
        service = PredictionService(MagicMock())
        match = _make_match(
            match_id=1,
            league_name="FIFA World Cup Qualifying - CONMEBOL",
            home_name="Peru",
            away_name="Brazil",
            home_type="NATIONAL",
            away_type="NATIONAL",
        )
        service.match_repo.get_by_id = MagicMock(return_value=match)
        with patch("app.services.prediction.national_team_prediction_service.NationalTeamPredictionService") as mock_svc:
            mock_svc.return_value.predict_match.return_value = _make_result(match)
            result = service.predict_match(match.id)

        self.assertIsNotNone(result)
        self.assertEqual(result.home_team, "Peru")
        self.assertEqual(result.away_team, "Brazil")
        mock_svc.assert_called_once()
        mock_svc.return_value.predict_match.assert_called_once_with(match.id, force=False)

    def test_predict_match_keeps_club_flow_intact(self) -> None:
        service = PredictionService(MagicMock())
        match = _make_match(
            match_id=2,
            league_name="La Liga",
            home_name="Barcelona",
            away_name="Real Madrid",
            home_type="CLUB",
            away_type="CLUB",
            home_key="la-liga",
            away_key="la-liga",
        )
        service.match_repo.get_by_id = MagicMock(return_value=match)
        service.predict_national_match = MagicMock(side_effect=AssertionError("No debe usar flujo nacional"))
        service._predict_cross_league = MagicMock(side_effect=AssertionError("No debe usar flujo cross-league"))

        captured: dict[str, object] = {}

        def fake_predict_from_pool(
            match_obj,
            league_ids,
            *,
            force=False,
            data_quality_prefix="club",
            min_matches=30,
            training_window_days=365,
            fallback_league_ids=None,
            home_advantage_override=None,
        ):
            captured["match"] = match_obj
            captured["league_ids"] = league_ids
            captured["force"] = force
            captured["data_quality_prefix"] = data_quality_prefix
            captured["min_matches"] = min_matches
            captured["training_window_days"] = training_window_days
            captured["fallback_league_ids"] = fallback_league_ids
            captured["home_advantage_override"] = home_advantage_override
            return _make_result(match_obj)

        service._resolve_league_ids_for_key = MagicMock(return_value=[5, 14])
        service._predict_from_league_pool = fake_predict_from_pool

        result = service.predict_match(match.id)

        self.assertIsNotNone(result)
        self.assertEqual(result.home_team, "Barcelona")
        self.assertEqual(result.away_team, "Real Madrid")
        self.assertIs(captured["match"], match)
        self.assertEqual(captured["league_ids"], [5, 14])
        self.assertEqual(captured["data_quality_prefix"], "club")
        self.assertEqual(captured["min_matches"], 30)
        self.assertEqual(captured["training_window_days"], 365)
        self.assertIsNone(captured["fallback_league_ids"])
        self.assertIsNone(captured["home_advantage_override"])
        service._resolve_league_ids_for_key.assert_called_once_with("la-liga")

    def test_competition_key_normalizes_accents(self) -> None:
        service = PredictionService(MagicMock())
        self.assertEqual(service._competition_key_for_league_name("Copa AmÚrica"), "copa-america")

    def test_predict_cross_league_neutral_venue_does_not_use_home_advantage(self) -> None:
        service = PredictionService(MagicMock())
        match = _make_match(
            match_id=4,
            league_name="Final Internacional",
            home_name="Argentina",
            away_name="France",
            home_type="CLUB",
            away_type="CLUB",
            home_key="argentina-primera",
            away_key="ligue-1",
        )
        service.model_repo.get_or_create = MagicMock(return_value=SimpleNamespace(id=1))
        service.prediction_repo.latest_for_match_and_model = MagicMock(return_value=None)
        service.feature_repo.upsert = MagicMock()
        service.rating_repo.upsert_by_match = MagicMock()
        service._build_calibrator = MagicMock(return_value=SimpleNamespace(is_fitted=False))
        service._calibrate_1x2 = MagicMock(side_effect=lambda p1, px, p2, league_id=None: (p1, px, p2))
        service._calibrate_ou25 = MagicMock(side_effect=lambda over, under, league_id=None: (over, under))
        service._calibrate_btts = MagicMock(side_effect=lambda yes, no, league_id=None: (yes, no))
        service._to_result = MagicMock(return_value="ok")
        service.prediction_repo.create = MagicMock(return_value=SimpleNamespace())
        service.league_strength_repo.get_coefficient = MagicMock(side_effect=[1.0, 1.0])

        params_home = SimpleNamespace(
            attack={match.home_team_id: 0.4},
            defense={match.home_team_id: -0.1},
            home_advantage=0.35,
            rho=0.02,
            teams=[match.home_team_id],
        )
        params_away = SimpleNamespace(
            attack={match.away_team_id: 0.2},
            defense={match.away_team_id: -0.05},
            home_advantage=0.15,
            rho=0.04,
            teams=[match.away_team_id],
        )
        service._fit_domestic_model = MagicMock(side_effect=[params_home, params_away])

        captured: dict[str, float] = {}

        def fake_predict_from_lambdas(lambda_home, lambda_away, rho):
            captured["lambda_home"] = lambda_home
            captured["lambda_away"] = lambda_away
            captured["rho"] = rho
            return {
                "lambda_home": lambda_home,
                "lambda_away": lambda_away,
                "p_home": 0.4,
                "p_draw": 0.3,
                "p_away": 0.3,
                "p_over_1_5": 0.7,
                "p_under_1_5": 0.3,
                "p_over_2_5": 0.5,
                "p_under_2_5": 0.5,
                "p_over_3_5": 0.2,
                "p_under_3_5": 0.8,
                "p_btts_yes": 0.45,
                "p_btts_no": 0.55,
                "xg_home": lambda_home,
                "xg_away": lambda_away,
                "top_scorelines": {"1-0": 0.1},
            }

        with patch("app.services.prediction.prediction_service.DixonColesModel.predict_from_lambdas", side_effect=fake_predict_from_lambdas):
            result = service._predict_cross_league(
                match,
                "argentina-primera",
                "ligue-1",
                is_neutral_venue=True,
            )

        self.assertEqual(result, "ok")
        self.assertAlmostEqual(captured["lambda_home"], 1.4190675485932571)
        self.assertAlmostEqual(captured["lambda_away"], 1.1051709180756477)
