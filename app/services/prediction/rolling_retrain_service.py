"""
Rolling retraining service — incrementally update team ratings after each match.

For each finished match (chronological order), trains Dixon-Coles on all prior
matches and persists the resulting attack/defense/rating per team to team_ratings.

No data leakage: the model only sees matches strictly before the target.
Compatible with PredictionService and BacktestingService (no modifications).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.team_rating_repository import TeamRatingRepository
from app.services.prediction.dixon_coles import DixonColesModel, MatchData
from config import HOME_ADVANTAGE, TIME_DECAY, XG_REG_WEIGHT

logger = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles_rolling"
MODEL_DESCRIPTION = "Dixon-Coles rolling retrain — snapshot per match"
MIN_TRAINING = 30

# Validation bounds
MAX_ATTACK_DEFENSE = 5.0


@dataclass
class RollingRetrainReport:
    """Summary of a rolling retrain run."""
    total_matches: int = 0
    processed: int = 0
    skipped_insufficient: int = 0
    skipped_existing: int = 0
    teams_updated: int = 0
    params_clipped: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "PERSISTIDO"
        lines = [
            "=" * 60,
            f"  ROLLING RETRAIN REPORT ({mode})",
            "=" * 60,
            f"  Partidos totales       : {self.total_matches}",
            f"  Procesados (snapshots) : {self.processed}",
            f"  Omitidos (< {MIN_TRAINING} hist)  : {self.skipped_insufficient}",
            f"  Omitidos (ya existen)  : {self.skipped_existing}",
            f"  Equipos actualizados   : {self.teams_updated}",
            f"  Params recortados (±{MAX_ATTACK_DEFENSE}) : {self.params_clipped}",
            "=" * 60,
        ]
        return "\n".join(lines)


class RollingRetrainService:
    """Walk-forward incremental retraining with DB persistence."""

    def __init__(
        self,
        db: Session,
        league_id: int | None = None,
        from_date: datetime | None = None,
        dry_run: bool = False,
    ) -> None:
        self.db = db
        self.league_id = league_id
        self.from_date = from_date
        self.dry_run = dry_run
        self.model_repo = ModelRepository(db)
        self.rating_repo = TeamRatingRepository(db)

    def run(self) -> RollingRetrainReport:
        report = RollingRetrainReport(dry_run=self.dry_run)

        model_rec = self.model_repo.get_or_create(
            name=MODEL_NAME,
            description=MODEL_DESCRIPTION,
        )
        if not self.dry_run:
            self.db.flush()

        matches = self._load_finished_matches()
        report.total_matches = len(matches)
        logger.info("Rolling retrain: %d partidos terminados", len(matches))

        all_ids = [m.id for m in matches]
        xg_map = self._load_xg_map(all_ids)

        for i, target in enumerate(matches):
            # Optionally skip matches before from_date
            if self.from_date and target.utc_date and target.utc_date < self.from_date:
                continue

            # Skip if already processed (idempotent)
            if not self.dry_run and self.rating_repo.exists_for_match(
                model_id=model_rec.id, as_of_match_id=target.id
            ):
                report.skipped_existing += 1
                continue

            # Training set: all matches before this one
            training_pool = matches[:i]
            if len(training_pool) < MIN_TRAINING:
                report.skipped_insufficient += 1
                continue

            # Build training data with time-decay
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
                report.skipped_insufficient += 1
                continue

            # Build xG priors
            xg_priors: dict[int, tuple[float, float]] = {}
            for tid in set(xg_for_lists) & set(xg_against_lists):
                avg_for = sum(xg_for_lists[tid]) / len(xg_for_lists[tid])
                avg_against = sum(xg_against_lists[tid]) / len(xg_against_lists[tid])
                xg_priors[tid] = (avg_for, avg_against)

            # Fit Dixon-Coles
            dc = DixonColesModel(time_decay=TIME_DECAY, home_adv_init=HOME_ADVANTAGE)
            try:
                params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=XG_REG_WEIGHT)
            except ValueError:
                report.skipped_insufficient += 1
                continue

            # Validate and persist per-team ratings
            as_of = target.utc_date or datetime.min
            teams_in_snapshot = 0

            for tid in params.teams:
                att = params.attack.get(tid, 0.0)
                dfn = params.defense.get(tid, 0.0)

                # Clamp to ±MAX_ATTACK_DEFENSE
                if abs(att) > MAX_ATTACK_DEFENSE or abs(dfn) > MAX_ATTACK_DEFENSE:
                    report.params_clipped += 1
                    att = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, att))
                    dfn = max(-MAX_ATTACK_DEFENSE, min(MAX_ATTACK_DEFENSE, dfn))

                # home_advantage validation: keep as-is from optimizer (no clamp)
                composite = att - dfn

                if not self.dry_run:
                    self.rating_repo.upsert_by_match(
                        model_id=model_rec.id,
                        team_id=tid,
                        as_of_match_id=target.id,
                        rating=composite,
                        attack=att,
                        defense=dfn,
                        as_of_date=as_of,
                    )
                teams_in_snapshot += 1

            report.processed += 1
            report.teams_updated += teams_in_snapshot

            if not self.dry_run and report.processed % 25 == 0:
                self.db.commit()

            if report.processed % 50 == 0:
                logger.info(
                    "Rolling retrain: %d/%d procesados (%d equipos)",
                    report.processed, report.total_matches, report.teams_updated,
                )

        # Final commit
        if not self.dry_run:
            self.db.commit()

        return report

    # ── DB helpers ────────────────────────────────────────────────────

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
