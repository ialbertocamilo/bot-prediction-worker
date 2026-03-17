"""Quick league summary with IDs and xG coverage."""
import sys
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.models.football.match_stats import MatchStats
from sqlalchemy import func

db = SessionLocal()
results = (
    db.query(
        League.id,
        League.name,
        func.count(Match.id),
        func.count(MatchStats.id),
    )
    .join(Match, Match.league_id == League.id)
    .outerjoin(MatchStats, MatchStats.match_id == Match.id)
    .filter(Match.status == "FINISHED")
    .group_by(League.id, League.name)
    .all()
)

print(f"{'ID':>3}  {'League':<35} {'FIN':>5}  {'Stats':>6}  {'xG%':>5}")
print("-" * 65)
for lid, name, fin, stats in sorted(results, key=lambda x: -x[2]):
    pct = stats / (fin * 2) * 100 if fin > 0 else 0
    print(f"{lid:>3}  {name:<35} {fin:>5}  {stats:>6}  {pct:>4.0f}%")
db.close()
