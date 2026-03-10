"""Quick script to check league and match data for dedup analysis."""
from app.db.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("=== LEAGUES ===")
for r in db.execute(text("SELECT id, name, country FROM leagues ORDER BY id")).fetchall():
    print(r)

print("\n=== MATCHES PER LEAGUE/STATUS ===")
for r in db.execute(text(
    "SELECT league_id, status, count(*) FROM matches GROUP BY league_id, status ORDER BY league_id, status"
)).fetchall():
    print(r)

print("\n=== EXTERNAL IDS FOR LEAGUES ===")
for r in db.execute(text(
    "SELECT entity_type, canonical_id, source_id, external_id FROM external_ids WHERE entity_type='league' ORDER BY canonical_id"
)).fetchall():
    print(r)

print("\n=== SOURCES ===")
for r in db.execute(text("SELECT id, name FROM sources ORDER BY id")).fetchall():
    print(r)

db.close()
