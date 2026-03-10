"""Quick DB status check."""
import sys
from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()

total = db.execute(text("SELECT count(*) FROM matches")).scalar()
sys.stderr.write(f"Total partidos: {total}\n")

rows = db.execute(text("SELECT status, count(*) FROM matches GROUP BY status ORDER BY count(*) DESC")).fetchall()
for r in rows:
    sys.stderr.write(f"  {r[0]}: {r[1]}\n")

upcoming = db.execute(text("SELECT count(*) FROM matches WHERE status = 'SCHEDULED' AND utc_date >= NOW()")).scalar()
sys.stderr.write(f"\nPartidos SCHEDULED futuros: {upcoming}\n")

latest = db.execute(text("SELECT MAX(utc_date) FROM matches")).scalar()
sys.stderr.write(f"Fecha del ultimo partido: {latest}\n")

earliest = db.execute(text("SELECT MIN(utc_date) FROM matches")).scalar()
sys.stderr.write(f"Fecha del primer partido: {earliest}\n")

now = db.execute(text("SELECT NOW()")).scalar()
sys.stderr.write(f"Hoy es: {now}\n")

league = db.execute(text("SELECT id, name, country FROM leagues")).fetchall()
sys.stderr.write("\nLigas en la DB:\n")
for l in league:
    sys.stderr.write(f"  Liga {l[0]}: {l[1]} ({l[2]})\n")

sys.stderr.write("\nUltimos 5 partidos por fecha:\n")
rows = db.execute(text("SELECT id, utc_date, status FROM matches ORDER BY utc_date DESC LIMIT 5")).fetchall()
for r in rows:
    sys.stderr.write(f"  ID={r[0]} fecha={r[1]} status={r[2]}\n")

sys.stderr.write("\nProximos 5 SCHEDULED:\n")
rows = db.execute(text("SELECT id, utc_date, status FROM matches WHERE status = 'SCHEDULED' ORDER BY utc_date ASC LIMIT 5")).fetchall()
if not rows:
    sys.stderr.write("  (ninguno)\n")
for r in rows:
    sys.stderr.write(f"  ID={r[0]} fecha={r[1]} status={r[2]}\n")

db.close()
sys.stderr.write("DONE\n")
