"""Quick test: pick 3 dates with missing matches and check SofaScore response."""
from datetime import date
from dotenv import load_dotenv
load_dotenv()
from collections import defaultdict
from difflib import SequenceMatcher
from sqlalchemy import select
from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.db.session import SessionLocal
from app.providers.factory import ProviderFactory

db = SessionLocal()
provider = ProviderFactory.create("sofascore")

ids_with = set(r.match_id for r in db.execute(select(MatchStats.match_id).distinct()))
stmt = select(Match).where(Match.status == 'FINISHED').where(Match.home_goals.isnot(None))
needing = [m for m in db.scalars(stmt).all() if m.id not in ids_with]

by_date = defaultdict(list)
for m in needing:
    d = m.utc_date.date() if hasattr(m.utc_date, 'date') else m.utc_date
    by_date[d].append(m)

# Pick 3 sample dates: one old (2024), one mid (2025), one recent (2026)
test_dates = [date(2024, 10, 5), date(2025, 4, 12), date(2026, 2, 15)]

for td in test_dates:
    # Find closest available date
    if td not in by_date:
        closest = min(by_date.keys(), key=lambda d: abs((d - td).days))
        td = closest

    db_matches = by_date[td]
    print(f"\n{'='*60}")
    print(f"Date: {td} — {len(db_matches)} DB matches needing stats")

    for m in db_matches[:3]:
        h = m.home_team.name if m.home_team else "?"
        a = m.away_team.name if m.away_team else "?"
        print(f"  DB: {h} vs {a}")

    try:
        events = provider.get_events_for_date(td)
        print(f"  SofaScore finished events: {len(events)}")
        if events:
            for ev in events[:5]:
                print(f"    SC: {ev['home_team']} vs {ev['away_team']}")

            # Try to find best fuzzy match for each DB match
            for m in db_matches[:3]:
                db_h = (m.home_team.name if m.home_team else "?").lower()
                db_a = (m.away_team.name if m.away_team else "?").lower()
                best = 0
                best_ev = None
                for ev in events:
                    sh = SequenceMatcher(None, db_h, ev['home_team'].lower()).ratio()
                    sa = SequenceMatcher(None, db_a, ev['away_team'].lower()).ratio()
                    sc = min(sh, sa)
                    if sc > best:
                        best = sc
                        best_ev = ev
                if best_ev:
                    print(f"  Best for '{m.home_team.name} vs {m.away_team.name}':")
                    print(f"    → '{best_ev['home_team']} vs {best_ev['away_team']}' (min_ratio={best:.3f})")
        else:
            print("    (no events)")
    except Exception as e:
        print(f"  ERROR: {e}")

db.close()
