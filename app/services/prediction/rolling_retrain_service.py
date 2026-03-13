"""
Rolling retraining service — incrementally update team ratings after each match.

For each finished match (chronological order), trains Dixon-Coles on all prior
matches and persists the resulting attack/defense/rating per team to team_ratings.

No data leakage: the model only sees matches strictly before the target.
Compatible with PredictionService and BacktestingService (no modifications).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.repositories.prediction.league_hyperparams_repository import LeagueHyperparamsRepository
from app.repositories.prediction.model_repository import ModelRepository
from app.repositories.prediction.team_rating_repository import TeamRatingRepository
from app.services.prediction.dixon_coles import DixonColesModel, MatchData
from app.services.prediction.training_data import build_training_data, load_xg_map
from config import HOME_ADVANTAGE, TIME_DECAY, XG_REG_WEIGHT, MIN_XG_MATCHES

logger = logging.getLogger(__name__)

MODEL_NAME = "dixon_coles_v1"
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
        self.hp_repo = LeagueHyperparamsRepository(db)

    def _league_params(self) -> tuple[float, float, float]:
        """Return (time_decay, xg_reg_weight, home_advantage) for the league."""
        if self.league_id is not None:
            hp = self.hp_repo.get_by_league(self.league_id)
            td = hp.time_decay if hp and hp.time_decay is not None else TIME_DECAY
            xg_w = hp.xg_reg_weight if hp and hp.xg_reg_weight is not None else XG_REG_WEIGHT
            ha = hp.home_advantage if hp and hp.home_advantage is not None else HOME_ADVANTAGE
            return td, xg_w, ha
        return TIME_DECAY, XG_REG_WEIGHT, HOME_ADVANTAGE

    def run(self) -> RollingRetrainReport:
        report = RollingRetrainReport(dry_run=self.dry_run)

        time_decay, xg_reg_weight, home_advantage = self._league_params()

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
        xg_map = load_xg_map(self.db, all_ids)

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

            ref_ts = target.utc_date
            match_data, xg_priors = build_training_data(
                training_pool, ref_ts, time_decay, xg_map, MIN_XG_MATCHES,
            )

            if len(match_data) < MIN_TRAINING:
                report.skipped_insufficient += 1
                continue

            # Fit Dixon-Coles
            dc = DixonColesModel(time_decay=time_decay, home_adv_init=home_advantage)
            try:
                params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=xg_reg_weight)
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
                self.db.flush()

            if report.processed % 50 == 0:
                logger.info(
                    "Rolling retrain: %d/%d procesados (%d equipos)",
                    report.processed, report.total_matches, report.teams_updated,
                )

        # Final flush — caller is responsible for commit.
        if not self.dry_run:
            self.db.flush()

        return report

    # ── DB helpers ────────────────────────────────────────────────────

    def _load_finished_matches(self) -> list[Match]:
        stmt = (
            select(Match)
            .where(Match.status == "FINISHED")
            .where(Match.home_goals.isnot(None))
            .where(Match.away_goals.isnot(None))
            .order_by(Match.utc_date.asc())
            .options(noload("*"))
        )
        if self.league_id is not None:
            stmt = stmt.where(Match.league_id == self.league_id)
        return list(self.db.scalars(stmt).all())
