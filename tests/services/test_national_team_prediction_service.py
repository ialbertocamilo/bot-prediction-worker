from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.services.prediction.national_team_prediction_service import (
    NationalTeamPredictionService,
)


def _match(*, league_name: str, round_value: str | None = None):
    return SimpleNamespace(
        id=1,
        league_id=10,
        season_id=None,
        league=SimpleNamespace(name=league_name),
        utc_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
        status="SCHEDULED",
        round=round_value,
        home_team_id=101,
        away_team_id=202,
        home_team=SimpleNamespace(team_type="NATIONAL", national_team_key="argentina", name="Argentina"),
        away_team=SimpleNamespace(team_type="NATIONAL", national_team_key="france", name="France"),
    )


class NationalTeamPredictionServiceTests(unittest.TestCase):
    def test_infer_friendly(self) -> None:
        m = _match(league_name="International Friendly")
        self.assertTrue(NationalTeamPredictionService._infer_is_friendly(m))

    def test_infer_neutral_venue_world_cup(self) -> None:
        m = _match(league_name="FIFA World Cup")
        self.assertTrue(NationalTeamPredictionService._infer_is_neutral_venue(m))

    def test_infer_neutral_venue_copa_america(self) -> None:
        m = _match(league_name="Copa América")
        self.assertTrue(NationalTeamPredictionService._infer_is_neutral_venue(m))

    def test_neutral_venue_does_not_add_home_advantage(self) -> None:
        svc = NationalTeamPredictionService(MagicMock())
        m = _match(league_name="FIFA World Cup")

        svc.match_repo.get_by_id = MagicMock(return_value=m)
        svc.model_repo.get_or_create = MagicMock(return_value=SimpleNamespace(id=1))
        svc.prediction_repo.latest_for_match_and_model = MagicMock(return_value=None)
        svc.feature_repo.upsert = MagicMock()
        svc.rating_repo.upsert_by_match = MagicMock()
        svc.prediction_repo.create = MagicMock(return_value=SimpleNamespace(
            p_home=0.33, p_draw=0.34, p_away=0.33,
            p_over_1_5=0.7, p_under_1_5=0.3,
            p_over_2_5=0.5, p_under_2_5=0.5,
            p_over_3_5=0.2, p_under_3_5=0.8,
            p_btts_yes=0.45, p_btts_no=0.55,
            xg_home=1.28, xg_away=1.28,
            top_scorelines={"1-1": 12.0},
            data_quality="x",
        ))

        svc._rating_for_team = MagicMock(side_effect=[1500.0, 1500.0])
        svc._recent_form = MagicMock(side_effect=[SimpleNamespace(points_per_game=1.0), SimpleNamespace(points_per_game=1.0)])
        svc._rest_days = MagicMock(side_effect=[7.0, 7.0])
        svc._base_goals_from_history = MagicMock(return_value=2.56)

        captured: dict[str, float] = {}

        def fake_predict_from_lambdas(lambda_home, lambda_away, rho=0.0):
            captured["lambda_home"] = lambda_home
            captured["lambda_away"] = lambda_away
            return {
                "p_home": 0.33,
                "p_draw": 0.34,
                "p_away": 0.33,
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
                "top_scorelines": {"1-1": 12.0},
                "lambda_home": lambda_home,
                "lambda_away": lambda_away,
            }

        with patch("app.services.prediction.national_team_prediction_service.DixonColesModel.predict_from_lambdas", side_effect=fake_predict_from_lambdas):
            svc.predict_match(m.id)

        self.assertAlmostEqual(captured["lambda_home"], 1.28, places=6)
        self.assertAlmostEqual(captured["lambda_away"], 1.28, places=6)

