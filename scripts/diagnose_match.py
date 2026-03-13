"""Diagnose why a prediction gives certain probabilities."""
import math
import sys
from app.db.session import SessionLocal
from sqlalchemy import select, func
from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from app.services.prediction.dixon_coles import DixonColesModel, MatchData
from app.services.prediction.training_data import build_training_data, load_xg_map
from config import TIME_DECAY, XG_REG_WEIGHT, HOME_ADVANTAGE, MIN_XG_MATCHES

MATCH_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 407

db = SessionLocal()

match = db.scalars(select(Match).where(Match.id == MATCH_ID)).first()
if not match:
    print(f"Match {MATCH_ID} not found")
    sys.exit(1)

lid = match.league_id
home_id = match.home_team_id
away_id = match.away_team_id
print(f"Match {MATCH_ID}: team {home_id} (H) vs team {away_id} (A)")
print(f"League ID: {lid}")
print(f"Match date: {match.utc_date}")
print()

# Count finished matches in this league
total = db.scalar(
    select(func.count(Match.id))
    .where(Match.league_id == lid)
    .where(Match.status == "FINISHED")
    .where(Match.home_goals.isnot(None))
    .where(Match.id != MATCH_ID)
    .where(Match.utc_date < match.utc_date)
)
print(f"Total FINISHED matches in league (before this match): {total}")

# Home team appearances
home_matches = db.scalars(
    select(Match)
    .where(Match.league_id == lid)
    .where(Match.status == "FINISHED")
    .where(Match.home_goals.isnot(None))
    .where(Match.id != MATCH_ID)
    .where(Match.utc_date < match.utc_date)
    .where((Match.home_team_id == home_id) | (Match.away_team_id == home_id))
    .order_by(Match.utc_date.desc())
).all()
print(f"\nHome team ({home_id}) UCL matches in training: {len(home_matches)}")
for m in home_matches[:8]:
    r = "H" if m.home_team_id == home_id else "A"
    opp = m.away_team_id if m.home_team_id == home_id else m.home_team_id
    res = f"{m.home_goals}-{m.away_goals}"
    ht = m.home_team.name if m.home_team else "?"
    at = m.away_team.name if m.away_team else "?"
    print(f"  {m.utc_date}  [{r}] {ht} {res} {at}")

# Away team appearances
away_matches = db.scalars(
    select(Match)
    .where(Match.league_id == lid)
    .where(Match.status == "FINISHED")
    .where(Match.home_goals.isnot(None))
    .where(Match.id != MATCH_ID)
    .where(Match.utc_date < match.utc_date)
    .where((Match.home_team_id == away_id) | (Match.away_team_id == away_id))
    .order_by(Match.utc_date.desc())
).all()
print(f"\nAway team ({away_id}) UCL matches in training: {len(away_matches)}")
for m in away_matches[:8]:
    r = "H" if m.home_team_id == away_id else "A"
    opp = m.away_team_id if m.home_team_id == away_id else m.home_team_id
    res = f"{m.home_goals}-{m.away_goals}"
    ht = m.home_team.name if m.home_team else "?"
    at = m.away_team.name if m.away_team else "?"
    print(f"  {m.utc_date}  [{r}] {ht} {res} {at}")

# Now reproduce the actual model fit
print("\n" + "="*60)
print("REPRODUCING MODEL FIT")
print("="*60)

training = db.scalars(
    select(Match)
    .where(Match.league_id == lid)
    .where(Match.status == "FINISHED")
    .where(Match.home_goals.isnot(None))
    .where(Match.away_goals.isnot(None))
    .where(Match.id != MATCH_ID)
    .where(Match.utc_date < match.utc_date)
    .order_by(Match.utc_date.asc())
).all()

ref_ts = match.utc_date
xg_map = load_xg_map(db, [m.id for m in training])
match_data, xg_priors = build_training_data(
    training, ref_ts, TIME_DECAY, xg_map, MIN_XG_MATCHES,
)

print(f"Match data points: {len(match_data)}")
print(f"xG priors for {len(xg_priors)} teams")
print(f"Config: TIME_DECAY={TIME_DECAY}, XG_REG_WEIGHT={XG_REG_WEIGHT}, HOME_ADV={HOME_ADVANTAGE}")

dc = DixonColesModel(time_decay=TIME_DECAY, home_adv_init=HOME_ADVANTAGE)
params = dc.fit(match_data, xg_priors=xg_priors, xg_weight=XG_REG_WEIGHT)

print(f"\nConverged: {params.converged}")
print(f"Home advantage: {params.home_advantage:.4f}")
print(f"Rho: {params.rho:.4f}")
print(f"Total teams in model: {len(params.teams)}")

# Show attack/defense for the two teams
avg_att = sum(params.attack.values()) / max(len(params.attack), 1)
avg_def = sum(params.defense.values()) / max(len(params.defense), 1)

a_h = params.attack.get(home_id, avg_att)
d_h = params.defense.get(home_id, avg_def)
a_a = params.attack.get(away_id, avg_att)
d_a = params.defense.get(away_id, avg_def)

is_h_known = home_id in params.attack
is_a_known = away_id in params.attack

print(f"\nHome team {home_id} ({'KNOWN' if is_h_known else 'FALLBACK TO AVG'}):")
print(f"  attack  = {a_h:.4f}")
print(f"  defense = {d_h:.4f}")
print(f"Away team {away_id} ({'KNOWN' if is_a_known else 'FALLBACK TO AVG'}):")
print(f"  attack  = {a_a:.4f}")
print(f"  defense = {d_a:.4f}")
print(f"Avg attack = {avg_att:.4f}, Avg defense = {avg_def:.4f}")

lam_h = math.exp(max(min(a_h + d_a + params.home_advantage, 5), -20))
lam_a = math.exp(max(min(a_a + d_h, 5), -20))

print(f"\nλ_home = exp({a_h:.4f} + {d_a:.4f} + {params.home_advantage:.4f}) = {lam_h:.4f}")
print(f"λ_away = exp({a_a:.4f} + {d_h:.4f}) = {lam_a:.4f}")

result = dc.predict_match(home_id, away_id, params)
print(f"\nResult: H={result['p_home']:.4f}  D={result['p_draw']:.4f}  A={result['p_away']:.4f}")

# Show all team ratings sorted
print(f"\n{'='*60}")
print("ALL TEAM RATINGS (sorted by attack)")
print(f"{'='*60}")
team_ratings = []
for tid in params.teams:
    a = params.attack.get(tid, 0)
    d = params.defense.get(tid, 0)
    team_ratings.append((tid, a, d, a - d))
team_ratings.sort(key=lambda x: x[1], reverse=True)

# Get team names
from app.db.models.football.team import Team
team_names = {}
for t in db.scalars(select(Team).where(Team.id.in_(params.teams))).all():
    team_names[t.id] = t.name

for tid, att, dfn, composite in team_ratings:
    name = team_names.get(tid, f"Team {tid}")
    marker = " <<<" if tid in (home_id, away_id) else ""
    print(f"  {name:<25} att={att:+.4f}  def={dfn:+.4f}  composite={composite:+.4f}{marker}")

db.close()
