from app.db.session import SessionLocal
from sqlalchemy import text

s = SessionLocal()
rows = s.execute(text(
    "SELECT l.name, "
    "  SUM(CASE WHEN m.status='FINISHED' THEN 1 ELSE 0 END) as fin, "
    "  SUM(CASE WHEN m.status='SCHEDULED' THEN 1 ELSE 0 END) as sch, "
    "  COUNT(m.id) as total, "
    "  MIN(m.utc_date)::text as earliest, "
    "  MAX(m.utc_date)::text as latest "
    "FROM matches m JOIN leagues l ON m.league_id=l.id "
    "GROUP BY l.name ORDER BY total DESC"
)).fetchall()

print(f"{'Liga':30s} {'FIN':>5s} {'SCH':>5s} {'TOT':>5s}  {'Desde':>10s}  {'Hasta':>10s}")
print("-" * 80)
for r in rows:
    print(f"{r[0]:30s} {r[1]:5d} {r[2]:5d} {r[3]:5d}  {r[4]:>10s}  {r[5]:>10s}")

total = s.execute(text("SELECT COUNT(*) FROM matches")).scalar()
fin = s.execute(text("SELECT COUNT(*) FROM matches WHERE status='FINISHED'")).scalar()
no_xg = s.execute(text(
    "SELECT COUNT(*) FROM matches m "
    "LEFT JOIN match_stats ms ON ms.match_id=m.id "
    "WHERE m.status='FINISHED' AND ms.id IS NULL"
)).scalar()
with_xg = fin - no_xg
pct = (with_xg / fin * 100) if fin else 0
print("-" * 80)
print(f"{'TOTAL':30s} {fin:5d} {total-fin:5d} {total:5d}")
print(f"\nCobertura xG: {with_xg}/{fin} ({pct:.1f}%)")
print(f"FINISHED sin xG stats: {no_xg}")
s.close()
print("\nSi faltan xG, vuelve a correr: python -m app.worker_main --sync-stats")
s.close()
