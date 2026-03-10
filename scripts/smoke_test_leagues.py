"""Smoke test for all endpoints."""
import json
import sys
import requests

BASE = "http://127.0.0.1:8000/api"

def test(name, url):
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        print(f"  OK {r.status_code} — {name}")
        return data
    except Exception as e:
        print(f"  FAIL — {name}: {e}")
        return None

print("=== Smoke Test ===\n")

# 1. Leagues
print("1. GET /api/leagues")
data = test("leagues", f"{BASE}/leagues")
if data:
    for lg in data.get("leagues", []):
        print(f"   {lg['index']:2d}. {lg['name']:25s} {lg['finished_matches']:3d} fin | {lg['scheduled_matches']:2d} sch  ids={lg['db_league_ids']}")

# 2. Matches for Premier League (index 3)
print("\n2. GET /api/matches?league=3  (Premier League)")
data = test("matches PL", f"{BASE}/matches?league=3")
if data:
    print(f"   {data['count']} matches returned")
    for m in data.get("matches", [])[:5]:
        print(f"   {m['number']}. {m['home_team']} vs {m['away_team']} ({m['utc_date']})")

# 3. Matches for Champions League (index 2)
print("\n3. GET /api/matches?league=2  (Champions League)")
data = test("matches CL", f"{BASE}/matches?league=2")
if data:
    print(f"   {data['count']} matches returned")
    for m in data.get("matches", [])[:5]:
        print(f"   {m['number']}. {m['home_team']} vs {m['away_team']} ({m['utc_date']})")

# 4. Predict match 1 from last /matches call
print("\n4. GET /api/predict?match_number=1")
data = test("predict", f"{BASE}/predict?match_number=1")
if data and data.get("prediction"):
    p = data["prediction"]
    print(f"   {p.get('home_team')} vs {p.get('away_team')}")
    print(f"   Home: {p.get('p_home',0)*100:.1f}%  Draw: {p.get('p_draw',0)*100:.1f}%  Away: {p.get('p_away',0)*100:.1f}%")

# 5. All matches
print("\n5. GET /api/matches  (all leagues)")
data = test("matches all", f"{BASE}/matches")
if data:
    print(f"   {data['count']} matches returned")

print("\n=== Done ===")
