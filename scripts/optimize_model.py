"""
Grid-search hyperparameter optimization for Dixon-Coles model.

Usage:
    python scripts/optimize_model.py --mode fine --league-id 13    # single league
    python scripts/optimize_model.py --mode coarse --all-leagues --save-db  # all domestic → DB
    python scripts/optimize_model.py --mode fine --all-leagues --save-db    # refined, slower

International tournaments (UCL, UEL, Libertadores) are skipped automatically
because they use the cross-league prediction path with domestic models.
After optimizing primary league_ids, secondary seasons inherit the same params.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

sys.path.insert(0, ".")

from sqlalchemy import func

from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.session import SessionLocal
from app.repositories.prediction.league_hyperparams_repository import (
    LeagueHyperparamsRepository,
)
from app.services.canonical_league_service import domestic_key_for_league_name
from app.services.prediction.hyperparameter_optimization_service import (
    HyperparameterOptimizationService,
    OptimizationReport,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Silence per-fit xG prior logs to reduce noise during grid search
logging.getLogger("app.services.prediction.dixon_coles").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _get_eligible_leagues(db, min_matches: int):
    """Return domestic leagues with at least *min_matches* finished matches.

    International tournaments (country=None in canonical groups) are excluded
    because they use the cross-league prediction path.
    """
    results = (
        db.query(League.id, League.name, func.count(Match.id).label("n"))
        .join(Match, Match.league_id == League.id)
        .filter(Match.status == "FINISHED")
        .group_by(League.id, League.name)
        .having(func.count(Match.id) >= min_matches)
        .order_by(func.count(Match.id).desc())
        .all()
    )
    # Keep only domestic leagues (international tournaments return None)
    domestic = [
        (lid, name, n)
        for lid, name, n in results
        if domestic_key_for_league_name(name) is not None
    ]
    return domestic


def _run_mode(svc: HyperparameterOptimizationService, mode: str, n_iter: int) -> OptimizationReport:
    if mode == "coarse":
        return svc.run_coarse()
    elif mode == "fine":
        return svc.run_fine()
    elif mode == "random":
        return svc.run_random(n_iter=n_iter)
    else:
        return svc.run()


def _save_to_db(db, hp_repo: LeagueHyperparamsRepository, league_id: int, best, league_name: str):
    """Persist the best hyperparams for a league to league_hyperparams table."""
    hp_repo.upsert(
        league_id=league_id,
        time_decay=best.time_decay,
        xg_reg_weight=best.xg_weight,
        home_advantage=best.home_adv,
        notes=f"Grid search LL={best.log_loss:.4f} Acc={best.accuracy:.2%} ({league_name})",
    )
    db.commit()
    logger.info(
        "Saved to league_hyperparams: league_id=%d TD=%.4f XG=%.1f HA=%.2f",
        league_id, best.time_decay, best.xg_weight, best.home_adv,
    )


def _propagate_to_siblings(db, hp_repo: LeagueHyperparamsRepository, optimized: dict):
    """Copy hyperparams to sibling league_ids sharing the same canonical key.

    *optimized* maps canonical_key → (best_result, league_name).
    Sibling league_ids are discovered by querying all leagues in the DB whose
    name maps to the same canonical key.
    """
    all_leagues = db.query(League.id, League.name).all()
    for lid, name in all_leagues:
        key = domestic_key_for_league_name(name)
        if key is None or key not in optimized:
            continue
        best, src_name = optimized[key]
        # Only write if this league_id doesn't already have optimized params
        existing = hp_repo.get_by_league(lid)
        if existing is not None:
            continue
        hp_repo.upsert(
            league_id=lid,
            time_decay=best.time_decay,
            xg_reg_weight=best.xg_weight,
            home_advantage=best.home_adv,
            notes=f"Inherited from {src_name} LL={best.log_loss:.4f}",
        )
        db.commit()
        logger.info(
            "Propagated to league_id=%d (%s) from %s",
            lid, name, src_name,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimización de hiperparámetros Dixon-Coles")
    parser.add_argument("--league-id", type=int, default=None, help="ID de liga")
    parser.add_argument("--all-leagues", action="store_true", help="Optimizar todas las ligas elegibles")
    parser.add_argument("--save-db", action="store_true", help="Guardar mejores params en league_hyperparams")
    parser.add_argument("--min-matches", type=int, default=100, help="Mín partidos acabados para elegir liga (default: 100)")
    parser.add_argument(
        "--mode",
        choices=["coarse", "fine", "full", "random"],
        default="fine",
        help="coarse: 27 combos | fine: coarse→refine (default) | full: 294 combos | random: n_iter samples",
    )
    parser.add_argument("--n-iter", type=int, default=30, help="Samples for --mode random")
    args = parser.parse_args()

    if not args.all_leagues and args.league_id is None:
        parser.error("Especifica --league-id N o --all-leagues")

    db = SessionLocal()
    try:
        hp_repo = LeagueHyperparamsRepository(db) if args.save_db else None
        all_lines: list[str] = []

        if args.all_leagues:
            leagues = _get_eligible_leagues(db, args.min_matches)
            if not leagues:
                print(f"No hay ligas domésticas con >= {args.min_matches} partidos acabados")
                return

            # Group by canonical key → pick primary (most matches) per competition
            from collections import defaultdict
            by_key: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
            for lid, name, n in leagues:
                key = domestic_key_for_league_name(name)
                if key:
                    by_key[key].append((lid, name, n))
            primaries: list[tuple[int, str, int]] = []
            for key, group in by_key.items():
                primary = max(group, key=lambda x: x[2])  # most matches
                primaries.append(primary)
            primaries.sort(key=lambda x: -x[2])

            print(f"\n{'='*70}")
            print(f"  ALL-LEAGUES OPTIMIZATION — {len(primaries)} domestic leagues, mode={args.mode}")
            print(f"{'='*70}")
            for lid, name, n in primaries:
                key = domestic_key_for_league_name(name)
                print(f"  {lid:>3}  {name:<40} {n:>4} matches  [{key}]")
            print(f"{'='*70}\n")

            consolidated: list[tuple[int, str, int, OptimizationReport]] = []
            optimized_by_key: dict[str, tuple] = {}  # key → (best, league_name)
            t_global = time.monotonic()

            for i, (lid, name, n) in enumerate(primaries, 1):
                header = f"[{i}/{len(primaries)}] {name} (id={lid}, {n} matches)"
                logger.info("=" * 60)
                logger.info(header)
                logger.info("=" * 60)

                svc = HyperparameterOptimizationService(db=db, league_id=lid)
                report = _run_mode(svc, args.mode, args.n_iter)
                consolidated.append((lid, name, n, report))

                if report.best:
                    logger.info(
                        "BEST for %s: TD=%.4f XG=%.1f HA=%.2f LL=%.4f",
                        name, report.best.time_decay, report.best.xg_weight,
                        report.best.home_adv, report.best.log_loss,
                    )
                    if hp_repo:
                        _save_to_db(db, hp_repo, lid, report.best, name)
                    key = domestic_key_for_league_name(name)
                    if key:
                        optimized_by_key[key] = (report.best, name)
                else:
                    logger.warning("No results for %s", name)

            # Propagate to sibling league_ids (secondary seasons)
            if hp_repo and optimized_by_key:
                logger.info("Propagating hyperparams to sibling league_ids...")
                _propagate_to_siblings(db, hp_repo, optimized_by_key)

            total_time = time.monotonic() - t_global

            # Build consolidated report
            lines = [
                "=" * 80,
                "  ALL-LEAGUES OPTIMIZATION REPORT",
                "=" * 80,
                f"  Ligas: {len(consolidated)}  |  Mode: {args.mode}  |  "
                f"Tiempo total: {total_time:.0f}s ({total_time/60:.1f} min)",
                f"  DB save: {'YES' if args.save_db else 'NO'}",
                "",
                f"  {'ID':>4}  {'Liga':<35} {'Matches':>7}  {'TD':>8}  {'XG':>6}  "
                f"{'HA':>6}  {'LogLoss':>8}  {'Brier':>7}  {'Acc':>7}",
                f"  {'─'*4}  {'─'*35} {'─'*7}  {'─'*8}  {'─'*6}  "
                f"{'─'*6}  {'─'*8}  {'─'*7}  {'─'*7}",
            ]
            for lid, name, n, report in consolidated:
                b = report.best
                if b:
                    lines.append(
                        f"  {lid:>4}  {name:<35} {b.total_matches:>7}  "
                        f"{b.time_decay:>8.4f}  {b.xg_weight:>6.1f}  "
                        f"{b.home_adv:>6.2f}  {b.log_loss:>8.4f}  "
                        f"{b.brier_score:>7.4f}  {b.accuracy:>6.2%}"
                    )
                else:
                    lines.append(f"  {lid:>4}  {name:<35} {'—':>7}  NO RESULTS")
            lines.append("=" * 80)
            summary = "\n".join(lines)

        else:
            # Single league
            svc = HyperparameterOptimizationService(db=db, league_id=args.league_id)
            report = _run_mode(svc, args.mode, args.n_iter)
            if args.save_db and hp_repo and report.best:
                league_row = db.query(League.name).filter(League.id == args.league_id).scalar()
                _save_to_db(db, hp_repo, args.league_id, report.best, league_row or "?")
            summary = report.summary()

        print(summary)
        with open("optimize_report.txt", "w", encoding="utf-8") as f:
            f.write(summary)
        print("\n[Report saved to optimize_report.txt]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
