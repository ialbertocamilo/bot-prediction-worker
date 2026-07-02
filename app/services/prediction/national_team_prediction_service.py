from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.prediction.prediction import Prediction
from app.repositories.football.match_repository import MatchRepository
from app.repositories.prediction.match_feature_repository import MatchFeatureRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.prediction_repository import PredictionRepository
from app.repositories.prediction.team_rating_repository import TeamRatingRepository
from app.services.prediction.dixon_coles import DixonColesModel
from app.services.prediction.schemas import MatchPredictionResult

logger = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles_v1"
MODEL_DESCRIPTION = "National teams (Elo/FIFA + form + rest + friendly/official + neutral/home)"


@dataclass(frozen=True, slots=True)
class NationalTeamPredictionConfig:
    base_total_goals: float = 2.55
    max_goals_share_shift: float = 0.25
    rating_scale: float = 400.0
    home_advantage_logit: float = 0.18
    friendly_weight: float = 0.6
    official_weight: float = 1.0
    form_matches: int = 5
    history_limit: int = 40
    history_window_days: int = 1460
    k_factor: float = 20.0


class NationalTeamPredictionService:
    def __init__(self, db: Session, *, config: NationalTeamPredictionConfig | None = None) -> None:
        self.db = db
        self.config = config or NationalTeamPredictionConfig()
        self.match_repo = MatchRepository(db)
        self.model_repo = ModelRepository(db)
        self.prediction_repo = PredictionRepository(db)
        self.feature_repo = MatchFeatureRepository(db)
        self.rating_repo = TeamRatingRepository(db)
        self._ranking_overrides = self._load_ranking_overrides()

    def predict_match(self, match_id: int, *, force: bool = False) -> MatchPredictionResult | None:
        match = self.match_repo.get_by_id(match_id)
        if match is None:
            return None

        home_type = getattr(match.home_team, "team_type", "CLUB") if match.home_team else "CLUB"
        away_type = getattr(match.away_team, "team_type", "CLUB") if match.away_team else "CLUB"
        if home_type != "NATIONAL" or away_type != "NATIONAL":
            return None

        model_rec = self.model_repo.get_or_create(
            name=MODEL_NAME,
            description=MODEL_DESCRIPTION,
        )
        existing = self.prediction_repo.latest_for_match_and_model(
            match_id=match.id,
            model_id=model_rec.id,
        )
        if existing is not None and not force:
            return self._to_result(existing, match)

        ref_ts: datetime = match.utc_date or datetime.now(timezone.utc)

        is_friendly = self._infer_is_friendly(match)
        is_neutral = self._infer_is_neutral_venue(match)

        home_key = getattr(match.home_team, "national_team_key", None) if match.home_team else None
        away_key = getattr(match.away_team, "national_team_key", None) if match.away_team else None

        home_rating = self._rating_for_team(match.home_team_id, home_key, before_date=ref_ts)
        away_rating = self._rating_for_team(match.away_team_id, away_key, before_date=ref_ts)

        home_form = self._recent_form(match.home_team_id, before_date=ref_ts, limit=self.config.form_matches)
        away_form = self._recent_form(match.away_team_id, before_date=ref_ts, limit=self.config.form_matches)

        rest_home = self._rest_days(match.home_team_id, before_date=ref_ts)
        rest_away = self._rest_days(match.away_team_id, before_date=ref_ts)

        base_goals = self._base_goals_from_history(
            match.home_team_id,
            match.away_team_id,
            before_date=ref_ts,
            default=self.config.base_total_goals,
        )
        if is_friendly:
            base_goals *= 0.97

        score = (home_rating - away_rating) / self.config.rating_scale
        score += 0.12 * (home_form.points_per_game - away_form.points_per_game)
        score += 0.02 * ((rest_home - rest_away) / 7.0)
        if not is_neutral:
            score += self.config.home_advantage_logit

        share_home = 1.0 / (1.0 + math.exp(-score))
        share_home = min(0.5 + self.config.max_goals_share_shift, max(0.5 - self.config.max_goals_share_shift, share_home))

        lambda_home = max(0.2, base_goals * share_home)
        lambda_away = max(0.2, base_goals * (1.0 - share_home))

        result = DixonColesModel.predict_from_lambdas(lambda_home, lambda_away, rho=0.0)

        model_id = model_rec.id
        rating_home = (home_rating - 1500.0) / 100.0
        rating_away = (away_rating - 1500.0) / 100.0
        try:
            self.feature_repo.upsert(
                match_id=match.id,
                model_id=model_id,
                lambda_home=result["lambda_home"],
                lambda_away=result["lambda_away"],
                rating_home=rating_home,
                rating_away=rating_away,
                rating_diff=rating_home - rating_away,
                home_goals_for_avg=result["xg_home"],
                away_goals_for_avg=result["xg_away"],
            )

            as_of = datetime.now(timezone.utc)
            self.rating_repo.upsert_by_match(
                model_id=model_id,
                team_id=match.home_team_id,
                as_of_match_id=match.id,
                rating=home_rating,
                as_of_date=as_of,
                attack=None,
                defense=None,
                season_id=match.season_id,
            )
            self.rating_repo.upsert_by_match(
                model_id=model_id,
                team_id=match.away_team_id,
                as_of_match_id=match.id,
                rating=away_rating,
                as_of_date=as_of,
                attack=None,
                defense=None,
                season_id=match.season_id,
            )

            prediction = self.prediction_repo.create(
                match_id=match.id,
                model_id=model_id,
                p_home=float(result["p_home"]),
                p_draw=float(result["p_draw"]),
                p_away=float(result["p_away"]),
                p_over_1_5=float(result["p_over_1_5"]),
                p_under_1_5=float(result["p_under_1_5"]),
                p_over_2_5=float(result["p_over_2_5"]),
                p_under_2_5=float(result["p_under_2_5"]),
                p_over_3_5=float(result["p_over_3_5"]),
                p_under_3_5=float(result["p_under_3_5"]),
                p_btts_yes=float(result["p_btts_yes"]),
                p_btts_no=float(result["p_btts_no"]),
                xg_home=float(result["xg_home"]),
                xg_away=float(result["xg_away"]),
                top_scorelines=result["top_scorelines"],
                data_quality=(
                    "national_elo_fifa_"
                    f"{'friendly' if is_friendly else 'official'}_"
                    f"{'neutral' if is_neutral else 'home'}"
                )[:100],
            )
            self.db.flush()
            return self._to_result(prediction, match)
        except Exception:
            self.db.rollback()
            logger.exception("NationalTeamPredictionService failed for match %d", match.id)
            return None

    @staticmethod
    def _to_result(pred: Prediction, match: Match) -> MatchPredictionResult:
        return MatchPredictionResult(
            match_id=match.id,
            home_team=match.home_team.name if match.home_team else "?",
            away_team=match.away_team.name if match.away_team else "?",
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league=match.league.name if match.league else "?",
            utc_date=match.utc_date,
            status=match.status,
            p_home=pred.p_home,
            p_draw=pred.p_draw,
            p_away=pred.p_away,
            p_over_1_5=pred.p_over_1_5,
            p_under_1_5=pred.p_under_1_5,
            p_over_2_5=pred.p_over_2_5,
            p_under_2_5=pred.p_under_2_5,
            p_over_3_5=pred.p_over_3_5,
            p_under_3_5=pred.p_under_3_5,
            p_btts_yes=pred.p_btts_yes,
            p_btts_no=pred.p_btts_no,
            xg_home=pred.xg_home,
            xg_away=pred.xg_away,
            top_scorelines=pred.top_scorelines,
            model=MODEL_NAME,
            data_quality=pred.data_quality,
        )

    def _load_ranking_overrides(self) -> dict[str, dict[str, float | int]]:
        path = os.getenv("NATIONAL_RANKINGS_PATH")
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            logger.exception("Could not load NATIONAL_RANKINGS_PATH=%s", path)
        return {}

    def _rating_for_team(self, team_id: int, team_key: str | None, *, before_date: datetime) -> float:
        if team_key:
            row = self._ranking_overrides.get(team_key)
            if row:
                if "elo" in row:
                    try:
                        return float(row["elo"])
                    except Exception:
                        pass
                if "fifa_points" in row:
                    try:
                        return 1500.0 + (float(row["fifa_points"]) - 1000.0) * 0.4
                    except Exception:
                        pass
                if "fifa_rank" in row:
                    try:
                        r = float(row["fifa_rank"])
                        return 2100.0 - r * 2.0
                    except Exception:
                        pass
        return self._elo_from_history(team_id, before_date=before_date)

    def _elo_from_history(self, team_id: int, *, before_date: datetime) -> float:
        cfg = self.config
        cutoff = before_date - timedelta(days=cfg.history_window_days)
        matches = self.match_repo.list_by_team(team_id, status="FINISHED", limit=cfg.history_limit)
        rows = [
            m for m in reversed(matches)
            if m.utc_date and m.utc_date < before_date and m.utc_date >= cutoff
            and m.home_goals is not None and m.away_goals is not None
            and getattr(m.home_team, "team_type", "CLUB") == "NATIONAL"
            and getattr(m.away_team, "team_type", "CLUB") == "NATIONAL"
        ]
        rating = 1500.0
        for m in rows:
            opponent_id = m.away_team_id if m.home_team_id == team_id else m.home_team_id
            opponent_key = (
                getattr(m.away_team, "national_team_key", None)
                if m.home_team_id == team_id else getattr(m.home_team, "national_team_key", None)
            )
            opp_rating = self._rating_for_team_cached(opponent_id, opponent_key)
            is_home = m.home_team_id == team_id
            is_friendly = self._infer_is_friendly(m)
            weight = cfg.friendly_weight if is_friendly else cfg.official_weight
            ha = 60.0 if is_home and not self._infer_is_neutral_venue(m) else 0.0
            exp_score = 1.0 / (1.0 + 10 ** (-(rating + ha - opp_rating) / 400.0))
            actual = 0.5
            if m.home_goals != m.away_goals:
                won = (m.home_goals > m.away_goals) if is_home else (m.away_goals > m.home_goals)
                actual = 1.0 if won else 0.0
            rating = rating + (cfg.k_factor * weight) * (actual - exp_score)
        return float(rating)

    def _rating_for_team_cached(self, team_id: int, team_key: str | None) -> float:
        if team_key:
            row = self._ranking_overrides.get(team_key)
            if row and "elo" in row:
                try:
                    return float(row["elo"])
                except Exception:
                    pass
        return 1500.0

    @dataclass(frozen=True, slots=True)
    class _Form:
        points_per_game: float
        goal_diff_per_game: float

    def _recent_form(self, team_id: int, *, before_date: datetime, limit: int) -> _Form:
        matches = self.match_repo.list_by_team(team_id, status="FINISHED", limit=self.config.history_limit)
        picked: list[Match] = []
        for m in matches:
            if not m.utc_date or m.utc_date >= before_date:
                continue
            if m.home_goals is None or m.away_goals is None:
                continue
            if getattr(m.home_team, "team_type", "CLUB") != "NATIONAL":
                continue
            if getattr(m.away_team, "team_type", "CLUB") != "NATIONAL":
                continue
            picked.append(m)
            if len(picked) >= limit:
                break

        if not picked:
            return self._Form(points_per_game=1.0, goal_diff_per_game=0.0)

        points = 0.0
        gd = 0.0
        for m in picked:
            is_home = m.home_team_id == team_id
            gf = float(m.home_goals if is_home else m.away_goals)
            ga = float(m.away_goals if is_home else m.home_goals)
            gd += gf - ga
            if gf > ga:
                points += 3.0
            elif gf == ga:
                points += 1.0

        n = float(len(picked))
        return self._Form(points_per_game=points / n, goal_diff_per_game=gd / n)

    def _rest_days(self, team_id: int, *, before_date: datetime) -> float:
        matches = self.match_repo.list_by_team(team_id, status="FINISHED", limit=self.config.history_limit)
        for m in matches:
            if not m.utc_date or m.utc_date >= before_date:
                continue
            if getattr(m.home_team, "team_type", "CLUB") != "NATIONAL":
                continue
            if getattr(m.away_team, "team_type", "CLUB") != "NATIONAL":
                continue
            delta = before_date - m.utc_date
            return max(0.0, delta.total_seconds() / 86400.0)
        return 7.0

    def _base_goals_from_history(
        self,
        home_team_id: int,
        away_team_id: int,
        *,
        before_date: datetime,
        default: float,
    ) -> float:
        matches = (
            self.match_repo.list_by_team(home_team_id, status="FINISHED", limit=self.config.history_limit)
            + self.match_repo.list_by_team(away_team_id, status="FINISHED", limit=self.config.history_limit)
        )
        cutoff = before_date - timedelta(days=self.config.history_window_days)
        totals: list[float] = []
        for m in matches:
            if not m.utc_date or m.utc_date >= before_date or m.utc_date < cutoff:
                continue
            if m.home_goals is None or m.away_goals is None:
                continue
            if getattr(m.home_team, "team_type", "CLUB") != "NATIONAL":
                continue
            if getattr(m.away_team, "team_type", "CLUB") != "NATIONAL":
                continue
            totals.append(float(m.home_goals + m.away_goals))
        if len(totals) < 8:
            return default
        totals = totals[-30:]
        return float(sum(totals) / len(totals))

    @staticmethod
    def _infer_is_friendly(match: Match) -> bool:
        name = (match.league.name if match.league else "") or ""
        n = name.lower()
        return "friendly" in n or "amistoso" in n

    @staticmethod
    def _infer_is_neutral_venue(match: Match) -> bool:
        name = (match.league.name if match.league else "") or ""
        n = name.lower()
        if "world cup" in n:
            return True
        if "european championship" in n or "euro" in n:
            return True
        if "copa america" in n or "copa am\u00e9rica" in n:
            return True
        if "nations league" in n and "final" in ((match.round or "").lower()):
            return True
        return False

