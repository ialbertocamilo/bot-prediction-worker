"""
Backtesting service — walk-forward evaluation of the Dixon-Coles model.

For each finished match (ordered by date), trains the model on all prior
matches and generates a prediction.  Compares predictions vs actual results
and computes Log Loss, Brier Score, Accuracy, and Calibration metrics.

No data leakage: the model never sees the match it is predicting.
Reads exclusively from the database.  Does NOT persist predictions.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.services.prediction.dixon_coles import DixonColesModel, MatchData
from config import TIME_DECAY, XG_REG_WEIGHT

logger = logging.getLogger(__name__)

MIN_TRAINING = 30


@dataclass
class MatchPrediction:
    """Single backtest prediction vs actual result."""
    match_id: int
    utc_date: datetime
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int
    p_home: float
    p_draw: float
    p_away: float
    actual_outcome: int  # 0=home, 1=draw, 2=away
    predicted_outcome: int  # 0=home, 1=draw, 2=away
    training_size: int


@dataclass
class BacktestReport:
    """Aggregate metrics from a backtest run."""
    total_matches: int = 0
    skipped_matches: int = 0
    log_loss: float = 0.0
    brier_score: float = 0.0
    accuracy: float = 0.0
    correct: int = 0
    # Calibration bins: {bin_label: (predicted_avg, actual_freq, count)}
    calibration: dict[str, tuple[float, float, int]] = field(default_factory=dict)
    predictions: list[MatchPrediction] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  BACKTEST REPORT — Dixon-Coles Walk-Forward",
            "=" * 60,
            f"  Partidos evaluados  : {self.total_matches}",
            f"  Partidos omitidos   : {self.skipped_matches}",
            f"  Log Loss (promedio) : {self.log_loss:.4f}",
            f"  Brier Score (prom.) : {self.brier_score:.4f}",
            f"  Accuracy 1X2        : {self.accuracy:.2%} ({self.correct}/{self.total_matches})",
            "",
            "  Calibración (prob predicha vs frecuencia real):",
            f"  {'Bin':<12} {'Pred Avg':>10} {'Real Freq':>10} {'Count':>8}",
            f"  {'-'*12} {'-'*10} {'-'*10} {'-'*8}",
        ]
        for label in sorted(self.calibration):
            pavg, freq, cnt = self.calibration[label]
            lines.append(
                f"  {label:<12} {pavg:>10.3f} {freq:>10.3f} {cnt:>8}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


class BacktestingService:
    def __init__(self, db: Session, league_id: int | None = None) -> None:
        self.db = db
        self.league_id = league_id

    def run(self) -> BacktestReport:
        """Execute walk-forward backtest and return metrics."""
        matches = self._load_finished_matches()
        logger.info("Backtest: %d partidos terminados cargados", len(matches))

        all_ids = [m.id for m in matches]
        xg_map = self._load_xg_map(all_ids)

        report = BacktestReport()
        eps = 1e-10  # clamp for log

        for i, target in enumerate(matches):
            # Training set: all matches before this one (strict temporal split)
            training_pool = matches[:i]

            if len(training_pool) < MIN_TRAINING:
                report.skipped_matches += 1
                continue

            # Build MatchData with time-decay relative to the target match date
            ref_ts = target.utc_date
            match_data: list[MatchData] = []
            xg_for_lists: dict[int, list[float]] = {}
            xg_against_lists: dict[int, list[float]] = {}

            for m in training_pool:
                if m.home_goals is None or m.away_goals is None:
                    continue
                days_ago = 0.0
                if m.utc_date and ref_ts:
                    delta = (ref_ts - m.utc_date).total_seconds() / 86400.0
                    days_ago = max(delta, 0.0)
                w = math.exp(-TIME_DECAY * days_ago)

                match_data.append(MatchData(
                    home_team_id=m.home_team_id,
                    away_team_id=m.away_team_id,
                    home_goals=m.home_goals,
                    away_goals=m.away_goals,
                    weight=w,
                ))

                pair = xg_map.get(m.id, {})
                h_xg = pair.get(m.home_team_id)
                a_xg = pair.get(m.away_team_id)
                if h_xg is not None and a_xg is not None:
                    xg_for_lists.setdefault(m.home_team_id, []).append(h_xg)
                    xg_against_lists.setdefault(m.home_team_id, []).append(a_xg)
                    xg_for_lists.setdefault(m.away_team_id, []).append(a_xg)
                    xg_against_lists.setdefault(m.away_team_id, []).append(h_xg)

            if len(match_data) < MIN_TRAINING:
                report.skipped_matches += 1
                continue

            # Ensure target teams appear in training data
            train_teams = {md.home_team_id for md in match_data} | {
                md.away_team_id for md in match_data
            }
            if target.home_team_id not in train_teams or target.away_team_id not in train_teams:
                report.skipped_matches += 1
                continue

            # Build xG priors
            xg_priors: dict[int, tuple[float, float]] = {}
            for tid in set(xg_for_lists) & set(xg_against_lists):
                avg_for = sum(xg_for_lists[tid]) / len(xg_for_lists[tid])
                avg_against = sum(xg_against_lists[tid]) / len(xg_against_lists[tid])
                xg_priors[tid] = (avg_for, avg_against)

            # Fit and predict
            dc = DixonColesModel(time_decay=TIME_DECAY)
            try:
                params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=XG_REG_WEIGHT)
            except ValueError:
                report.skipped_matches += 1
                continue

            result = dc.predict_match(target.home_team_id, target.away_team_id, params)

            p_h = result["p_home"]
            p_d = result["p_draw"]
            p_a = result["p_away"]

            # Actual outcome
            hg, ag = target.home_goals, target.away_goals
            if hg > ag:
                actual = 0
            elif hg == ag:
                actual = 1
            else:
                actual = 2

            predicted = [0, 1, 2][max(range(3), key=lambda k: [p_h, p_d, p_a][k])]

            mp = MatchPrediction(
                match_id=target.id,
                utc_date=target.utc_date,
                home_team_id=target.home_team_id,
                away_team_id=target.away_team_id,
                home_goals=hg,
                away_goals=ag,
                p_home=p_h,
                p_draw=p_d,
                p_away=p_a,
                actual_outcome=actual,
                predicted_outcome=predicted,
                training_size=len(match_data),
            )
            report.predictions.append(mp)
            report.total_matches += 1
            if predicted == actual:
                report.correct += 1

            if i % 25 == 0:
                logger.info(
                    "Backtest progreso: %d/%d (evaluados=%d, omitidos=%d)",
                    i, len(matches), report.total_matches, report.skipped_matches,
                )

        # Compute aggregate metrics
        if report.total_matches > 0:
            total_ll = 0.0
            total_bs = 0.0
            # Calibration: 10 bins by predicted probability
            cal_bins: dict[str, list[tuple[float, int]]] = {}

            for mp in report.predictions:
                probs = [mp.p_home, mp.p_draw, mp.p_away]
                actual_vec = [0.0, 0.0, 0.0]
                actual_vec[mp.actual_outcome] = 1.0

                # Log Loss (multiclass)
                total_ll += -math.log(max(probs[mp.actual_outcome], eps))

                # Brier Score (multiclass)
                total_bs += sum((p - a) ** 2 for p, a in zip(probs, actual_vec))

                # Calibration: bin the probability of the predicted class
                p_pred = probs[mp.predicted_outcome]
                hit = 1 if mp.predicted_outcome == mp.actual_outcome else 0
                bin_idx = min(int(p_pred * 10), 9)
                label = f"{bin_idx * 10:>2d}-{(bin_idx + 1) * 10:>2d}%"
                cal_bins.setdefault(label, []).append((p_pred, hit))

            report.log_loss = total_ll / report.total_matches
            report.brier_score = total_bs / report.total_matches
            report.accuracy = report.correct / report.total_matches

            for label, entries in cal_bins.items():
                avg_p = sum(e[0] for e in entries) / len(entries)
                freq = sum(e[1] for e in entries) / len(entries)
                report.calibration[label] = (avg_p, freq, len(entries))

        return report

    # ------------------------------------------------------------------ #
    #  DB helpers                                                         #
    # ------------------------------------------------------------------ #

    def _load_finished_matches(self) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.asc())
        )
        if self.league_id is not None:
            stmt = stmt.where(Match.league_id == self.league_id)
        return list(self.db.scalars(stmt).all())

    def _load_xg_map(self, match_ids: list[int]) -> dict[int, dict[int, float]]:
        if not match_ids:
            return {}
        # SQLAlchemy IN with large lists — batch if needed
        result: dict[int, dict[int, float]] = {}
        batch_size = 500
        for start in range(0, len(match_ids), batch_size):
            batch = match_ids[start: start + batch_size]
            stmt = (
                select(MatchStats.match_id, MatchStats.team_id, MatchStats.xg)
                .where(MatchStats.match_id.in_(batch))
                .where(MatchStats.xg.isnot(None))
            )
            for row in self.db.execute(stmt):
                result.setdefault(row.match_id, {})[row.team_id] = row.xg
        return result
