"""Diagnóstico: por qué sync-stats no matchea los 1255 partidos restantes."""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.db.session import SessionLocal
from app.providers.factory import ProviderFactory


def _match_team_name(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.8


def main():
    db = SessionLocal()
    provider = ProviderFactory.create("sofascore")

    # Get matches without stats
    finished_ids_with_stats = set(
        row.match_id for row in db.execute(
            select(MatchStats.match_id).distinct()
        )
    )

    stmt = (
        select(Match)
        .where(Match.status == "FINISHED")
        .where(Match.home_goals.isnot(None))
        .order_by(Match.utc_date.desc())
    )
    all_finished = list(db.scalars(stmt).all())
    needing = [m for m in all_finished if m.id not in finished_ids_with_stats]
    print(f"Partidos sin stats: {len(needing)}")

    # Group by date
    by_date: dict[date, list] = defaultdict(list)
    for m in needing:
        d = m.utc_date.date() if hasattr(m.utc_date, 'date') else m.utc_date
        by_date[d].append(m)

    # Sample 5 dates, show what SofaScore returns vs what DB expects
    sample_dates = sorted(by_date.keys(), reverse=True)[:5]

    for target_date in sample_dates:
        print(f"\n{'='*60}")
        print(f"Fecha: {target_date}")
        db_matches = by_date[target_date]
        print(f"  DB espera {len(db_matches)} partidos:")
        for m in db_matches:
            h = m.home_team.name if m.home_team else "?"
            a = m.away_team.name if m.away_team else "?"
            print(f"    [{m.id}] {h} vs {a}")

        try:
            events = provider.get_events_for_date(target_date)
        except Exception as e:
            print(f"  SofaScore ERROR: {e}")
            continue

        print(f"  SofaScore devuelve {len(events)} eventos terminados:")
        if not events:
            print(f"    (vacío)")
            continue

        # Show first 10 events
        for ev in events[:10]:
            sc_h = ev.get("home_team", "")
            sc_a = ev.get("away_team", "")
            # Check if any DB match matches
            matched = False
            for m in db_matches:
                db_h = m.home_team.name if m.home_team else "?"
                db_a = m.away_team.name if m.away_team else "?"
                if _match_team_name(sc_h, db_h) and _match_team_name(sc_a, db_a):
                    matched = True
                    break
            flag = "✓ MATCH" if matched else ""
            print(f"    {sc_h} vs {sc_a}  {flag}")
        if len(events) > 10:
            print(f"    ... y {len(events) - 10} más")

        # For unmatched DB entries, show best fuzzy score
        print(f"  Mejores candidatos fuzzy:")
        for m in db_matches[:3]:
            db_h = m.home_team.name if m.home_team else "?"
            db_a = m.away_team.name if m.away_team else "?"
            best_score = 0
            best_ev = None
            for ev in events:
                sc_h = ev.get("home_team", "")
                sc_a = ev.get("away_team", "")
                score_h = SequenceMatcher(None, db_h.lower(), sc_h.lower()).ratio()
                score_a = SequenceMatcher(None, db_a.lower(), sc_a.lower()).ratio()
                combined = (score_h + score_a) / 2
                if combined > best_score:
                    best_score = combined
                    best_ev = ev
            if best_ev:
                print(f"    DB: {db_h} vs {db_a}")
                print(f"    SC: {best_ev['home_team']} vs {best_ev['away_team']}")
                print(f"    Score: {best_score:.3f} (threshold: 0.8)")
            else:
                print(f"    DB: {db_h} vs {db_a} — NO events from SofaScore")

    db.close()


if __name__ == "__main__":
    main()
