import requests, json

# 1. Upcoming matches
print("=" * 60)
print("GET /predict/upcoming")
print("=" * 60)
r = requests.get("http://localhost:8001/predict/upcoming")
data = r.json()
print(f"Status: {r.status_code}")
print(f"Partidos: {data['count']}")
for m in data["matches"][:3]:
    print(f"\n  ID={m['match_id']} {m['home_team']} vs {m['away_team']}")
    print(f"  Fecha: {m['utc_date']}")
    p = m.get("prediction")
    if p:
        print(f"  H={p['p_home']*100:.1f}% D={p['p_draw']*100:.1f}% A={p['p_away']*100:.1f}%")
        print(f"  Fair odds: {p['fair_odds']}")

# 2. Single prediction
print("\n" + "=" * 60)
match_id = data["matches"][0]["match_id"]
print(f"GET /predict/{match_id}")
print("=" * 60)
r = requests.get(f"http://localhost:8001/predict/{match_id}")
d = r.json()
print(f"Status: {r.status_code}")
print(f"{d['home_team']} vs {d['away_team']}")
print(f"H={d['p_home']*100:.1f}% D={d['p_draw']*100:.1f}% A={d['p_away']*100:.1f}%")
print(f"xG: {d['xg_home']} - {d['xg_away']}")
print(f"Fair odds: {d['fair_odds']}")

# 3. With bookmaker odds
print("\n" + "=" * 60)
print(f"GET /predict/{match_id}?odds_home=1.85&odds_draw=3.40&odds_away=4.50")
print("=" * 60)
r = requests.get(f"http://localhost:8001/predict/{match_id}", params={
    "odds_home": 1.85, "odds_draw": 3.40, "odds_away": 4.50
})
d = r.json()
print(f"Status: {r.status_code}")
va = d.get("value_analysis", {})
print(f"Value bets found: {len(va.get('value_bets', []))}")
for m in va.get("all_markets", []):
    icon = "VALUE" if m["is_value"] else "    "
    print(f"  [{icon}] {m['market']}: modelo={m['model_prob']*100:.1f}% casa={m['implied_prob']*100:.1f}% EV={m['ev']*100:+.1f}% kelly={m['kelly_fraction']*100:.1f}%")
