"""Test the score-based matching system.

Tests 4 levels of defense:
  Level 1: Name similarity (normalize + tokens + fuzzy)
  Level 2: Score verification (goals must match)
  Level 3: Dual-team check (BOTH home AND away must pass)
  Level 4: Best-match + uniqueness (each event used once)
"""
import re, unicodedata
from difflib import SequenceMatcher
from types import SimpleNamespace

_TEAM_NOISE = frozenset({"fc","cf","sc","cd","ac","as","us","ss","rcd","afc","bsc","fk","sk","nk","pk","sv","tsv","vfl","vfb","de","del","fsv","tsg","1.","1899","1848","1860","04","05","09"})

def _strip_accents(s):
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def _normalize_team(name):
    name = _strip_accents(name.lower().strip())
    name = re.sub(r"[.\-'\"()]", " ", name)
    return re.sub(r"\s+", " ", name).strip()

def _core_tokens(name):
    tokens = set(_normalize_team(name).split())
    meaningful = {t for t in tokens if t not in _TEAM_NOISE and not t.isdigit()}
    return meaningful if meaningful else tokens

def _team_name_score(a, b):
    la, lb = a.lower().strip(), b.lower().strip()
    if la == lb: return 1.0
    na, nb = _normalize_team(a), _normalize_team(b)
    if na == nb: return 1.0
    if len(na) >= 4 and len(nb) >= 4:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if re.search(r"\b" + re.escape(shorter) + r"\b", longer):
            return 0.90
    ta, tb = _core_tokens(a), _core_tokens(b)
    if ta and tb:
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        matched = 0
        for s in smaller:
            for l in larger:
                if s == l or (len(s)>=4 and len(l)>=4 and SequenceMatcher(None,s,l).ratio() > 0.65):
                    matched += 1; break
        ratio = matched / len(smaller)
        if ratio > 0: return 0.5 + 0.4 * ratio
    return SequenceMatcher(None, na, nb).ratio()

_NAME_THRESHOLD = 0.45

def _find_best_match(db_match, db_home, db_away, events, used):
    db_hg, db_ag = db_match.home_goals, db_match.away_goals
    best, best_score, best_verified = None, 0.0, False
    best_h, best_a = 0.0, 0.0
    for ev in events:
        eid = ev["id"]
        if eid in used: continue
        h = _team_name_score(ev["home_team"], db_home)
        a = _team_name_score(ev["away_team"], db_away)
        if h < _NAME_THRESHOLD or a < _NAME_THRESHOLD: continue
        combined = (h + a) / 2
        score_ok = (db_hg is not None and ev.get("home_goals") is not None
                    and db_hg == ev["home_goals"] and db_ag == ev.get("away_goals"))
        if score_ok and not best_verified:
            best, best_score, best_verified = ev, combined, True
            best_h, best_a = h, a
        elif score_ok and best_verified and combined > best_score:
            best, best_score = ev, combined
            best_h, best_a = h, a
        elif not score_ok and not best_verified and combined > best_score:
            best, best_score = ev, combined
            best_h, best_a = h, a
    if best is None: return None
    if not best_verified and best_score < 0.85: return None
    if best_verified and min(best_h, best_a) < 0.75: return None
    return best

def make_db(hg, ag):
    return SimpleNamespace(home_goals=hg, away_goals=ag)

total_ok = 0
total_tests = 0

# ════════════════════════════════════════════════════════════════════
print("=" * 90)
print("TEST 1: Normal matches — ESPN names → SofaScore names")
print("        All should find the correct event via name + score")
print("=" * 90)

events_oct5 = [
    {"id": "1", "home_team": "Crystal Palace", "away_team": "Liverpool", "home_goals": 0, "away_goals": 1},
    {"id": "2", "home_team": "Arsenal", "away_team": "Southampton", "home_goals": 3, "away_goals": 1},
    {"id": "3", "home_team": "Brentford", "away_team": "Wolverhampton", "home_goals": 5, "away_goals": 3},
    {"id": "4", "home_team": "FC Bayern München", "away_team": "Eintracht Frankfurt", "home_goals": 3, "away_goals": 0},
    {"id": "5", "home_team": "FC St. Pauli", "away_team": "1. FSV Mainz 05", "home_goals": 0, "away_goals": 3},
    {"id": "6", "home_team": "Napoli", "away_team": "Roma", "home_goals": 1, "away_goals": 0},
    {"id": "7", "home_team": "Atlético de Madrid", "away_team": "Real Betis", "home_goals": 2, "away_goals": 1},
    {"id": "8", "home_team": "OGC Nice", "away_team": "Olympique Lyonnais", "home_goals": 1, "away_goals": 1},
    {"id": "9", "home_team": "TSG 1899 Hoffenheim", "away_team": "VfL Bochum 1848", "home_goals": 2, "away_goals": 0},
    {"id": "10", "home_team": "Paris Saint-Germain", "away_team": "RC Strasbourg", "home_goals": 4, "away_goals": 2},
    {"id": "11", "home_team": "CD Moquegua", "away_team": "Sporting Cristal", "home_goals": 0, "away_goals": 2},
]

test1 = [
    ("Brentford", "Wolverhampton Wanderers", 5, 3, "3"),
    ("Bayern Munich", "Eintracht Frankfurt", 3, 0, "4"),
    ("St. Pauli", "Mainz", 0, 3, "5"),
    ("Napoli", "AS Roma", 1, 0, "6"),
    ("Atletico Madrid", "Real Betis", 2, 1, "7"),
    ("Nice", "Lyon", 1, 1, "8"),
    ("TSG Hoffenheim", "VfL Bochum", 2, 0, "9"),
    ("Paris Saint-Germain", "RC Strasbourg", 4, 2, "10"),
    ("Deportivo Moquegua", "Sporting Cristal", 0, 2, "11"),
]

used = set()
for home, away, hg, ag, expected_id in test1:
    db = make_db(hg, ag)
    ev = _find_best_match(db, home, away, events_oct5, used)
    if ev: used.add(ev["id"])
    found_id = ev["id"] if ev else None
    correct = found_id == expected_id
    total_ok += correct; total_tests += 1
    flag = "OK" if correct else "FAIL <<<<<"
    line = f"  {home:30s} vs {away:25s} ({hg}-{ag}) → {str(found_id):4s}  {flag}"
    print(line)

# ════════════════════════════════════════════════════════════════════
print()
print("=" * 90)
print("TEST 2: Confusable names, DIFFERENT opponents and scores (realistic)")
print("        e.g. Man City plays Liverpool while Man United plays Tottenham")
print("        Each DB match should find ONLY its correct event, NOT the similar-named one")
print("=" * 90)

events_danger = [
    # Premier League matchday
    {"id": "A", "home_team": "Manchester United", "away_team": "Tottenham", "home_goals": 1, "away_goals": 2},
    {"id": "B", "home_team": "Manchester City", "away_team": "Liverpool", "home_goals": 3, "away_goals": 0},
    # La Liga matchday
    {"id": "C", "home_team": "Real Sociedad", "away_team": "Celta", "home_goals": 0, "away_goals": 0},
    {"id": "D", "home_team": "Real Madrid", "away_team": "Villarreal", "home_goals": 2, "away_goals": 1},
    # Copa Libertadores + La Liga same day
    {"id": "E", "home_team": "Barcelona SC", "away_team": "River Plate", "home_goals": 1, "away_goals": 3},
    {"id": "F", "home_team": "Barcelona", "away_team": "Girona", "home_goals": 4, "away_goals": 0},
    # Liga Argentina + Premier League same day
    {"id": "G", "home_team": "Arsenal de Sarandí", "away_team": "Boca Juniors", "home_goals": 0, "away_goals": 1},
    {"id": "H", "home_team": "Arsenal", "away_team": "Brighton", "home_goals": 2, "away_goals": 0},
    # Serie A + Copa Libertadores same day
    {"id": "I", "home_team": "Internacional", "away_team": "Flamengo", "home_goals": 1, "away_goals": 1},
    {"id": "J", "home_team": "Inter", "away_team": "Juventus", "home_goals": 3, "away_goals": 2},
]

test2 = [
    # DB matches — should find EXACTLY their correct event
    ("Manchester City", "Liverpool", 3, 0, "B"),         # NOT "A" (United)
    ("Manchester United", "Tottenham", 1, 2, "A"),       # NOT "B" (City)
    ("Real Madrid", "Villarreal", 2, 1, "D"),            # NOT "C" (Sociedad)
    ("Real Sociedad", "Celta", 0, 0, "C"),               # NOT "D" (Madrid)
    ("Barcelona", "Girona", 4, 0, "F"),                  # NOT "E" (Barcelona SC)
    ("Barcelona SC", "River Plate", 1, 3, "E"),          # NOT "F" (Barcelona ESP)
    ("Arsenal", "Brighton", 2, 0, "H"),                  # NOT "G" (Sarandí)
    ("Arsenal de Sarandí", "Boca Juniors", 0, 1, "G"),   # NOT "H" (Arsenal ENG)
    ("Inter", "Juventus", 3, 2, "J"),                    # NOT "I" (Internacional)
]

for home, away, hg, ag, expected_id in test2:
    db = make_db(hg, ag)
    ev = _find_best_match(db, home, away, events_danger, set())
    found_id = ev["id"] if ev else None
    correct = found_id == expected_id
    total_ok += correct; total_tests += 1
    flag = "OK" if correct else "FAIL <<<<<"
    print(f"  {home:30s} vs {away:25s} ({hg}-{ag}) → {str(found_id):4s}  {flag}")

# ════════════════════════════════════════════════════════════════════
print()
print("=" * 90)
print("TEST 3: Worst case — confusable name + SAME score + SAME opponent")
print("        This is almost impossible in reality but tests maximum robustness")
print("        DB match should NOT match the wrong event")
print("=" * 90)

events_worst = [
    {"id": "X", "home_team": "Manchester United", "away_team": "Everton", "home_goals": 2, "away_goals": 1},
]

test3_cases = [
    # Man City looking for 2-1 vs Everton — but only Man United 2-1 Everton exists
    # Name score: Man City vs Man United ≈ 0.70, Everton vs Everton = 1.0
    # Combined: (0.70 + 1.0) / 2 = 0.85. Score verified? YES.
    # This IS the edge case. Without perfect name matching this could false-positive.
    ("Manchester City", "Everton", 2, 1, None),
]

for home, away, hg, ag, expected_id in test3_cases:
    db = make_db(hg, ag)
    ev = _find_best_match(db, home, away, events_worst, set())
    found_id = ev["id"] if ev else None
    correct = found_id == expected_id
    total_ok += correct; total_tests += 1
    flag = "OK" if correct else "FAIL <<<<<"
    # Show details for this edge case
    h_s = _team_name_score("Manchester United", home)
    a_s = _team_name_score("Everton", away)
    print(f"  {home:30s} vs {away:25s} ({hg}-{ag}) → {str(found_id):4s}  {flag}")
    print(f"     name_scores: home={h_s:.2f}  away={a_s:.2f}  combined={(h_s+a_s)/2:.2f}")

# ════════════════════════════════════════════════════════════════════
print()
print("=" * 90)
print("TEST 4: Name score verification for reference")
print("=" * 90)

name_pairs = [
    ("Bayern Munich", "FC Bayern München", ">0.80"),
    ("Lyon", "Olympique Lyonnais", ">0.70"),
    ("Nice", "OGC Nice", ">0.80"),
    ("Mainz", "1. FSV Mainz 05", ">0.80"),
    ("Manchester City", "Manchester United", "<0.75"),
    ("Real Madrid", "Real Sociedad", "<0.75"),
    ("Arsenal", "Arsenal de Sarandí", "~0.90 *"),
    ("Barcelona", "Barcelona SC", "~0.90 *"),
    ("Inter", "Internacional", "<0.50"),
]

for a, b, note in name_pairs:
    s = _team_name_score(a, b)
    print(f"  {s:.3f}  {a:30s} ↔ {b:30s}  ({note})")
print("  * Arsenal/Barcelona: high name score BUT opponent never matches → safe")

# ════════════════════════════════════════════════════════════════════
print()
print("=" * 90)
print(f"TOTAL: {total_ok}/{total_tests} correct")
print("=" * 90)
if total_ok == total_tests:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {total_tests - total_ok}")
