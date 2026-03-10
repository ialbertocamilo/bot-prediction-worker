"""Quick smoke test for canonical league endpoints."""
import json
import urllib.request

BASE = "http://localhost:8000/api"

def get(path):
    r = urllib.request.urlopen(f"{BASE}{path}")
    return json.loads(r.read())

print("=== GET /api/leagues ===")
data = get("/leagues")
for lg in data["leagues"]:
    print(f"  {lg['index']}. {lg['name']} ({lg['country']}) — "
          f"{lg['finished_matches']} finished, {lg['scheduled_matches']} scheduled "
          f"[db_ids={lg['db_league_ids']}]")

print(f"\n=== GET /api/matches (all) — count={get('/matches')['count']} ===")
data = get("/matches")
for m in data["matches"][:5]:
    print(f"  {m['number']}. {m['home_team']} vs {m['away_team']} [{m['league']}]")
if data["count"] > 5:
    print(f"  ... +{data['count']-5} more")

print(f"\n=== GET /api/matches?league=1 — count={get('/matches?league=1')['count']} ===")
data = get("/matches?league=1")
for m in data["matches"][:5]:
    print(f"  {m['number']}. {m['home_team']} vs {m['away_team']} [{m['league']}]")

if data["count"] > 0:
    print(f"\n=== GET /api/predict?match_number=1 ===")
    pred = get("/predict?match_number=1")
    p = pred["prediction"]
    print(f"  {p['home_team']} vs {p['away_team']}")
    print(f"  H={p['p_home']:.1%}  D={p['p_draw']:.1%}  A={p['p_away']:.1%}")
    print(f"  xG: {p.get('xg_home',0):.2f} - {p.get('xg_away',0):.2f}")

print("\nAll endpoints OK!")
