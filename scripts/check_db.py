from app.db.session import SessionLocal
from sqlalchemy import text

s = SessionLocal()
r = s.execute(text(
    "SELECT l.name, COUNT(m.id) "
    "FROM matches m JOIN leagues l ON m.league_id=l.id "
    "GROUP BY l.name ORDER BY l.name"
)).fetchall()
for row in r:
    print(f"{row[0]:30s} {row[1]:5d}")
total = s.execute(text("SELECT COUNT(*) FROM matches")).scalar()
print(f"{'TOTAL':30s} {total:5d}")
s.close()
