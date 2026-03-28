"""Check crest_url for specific teams."""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()

# Check specific teams
teams = db.execute(text(
    "SELECT t.id, t.name, t.crest_url FROM teams t "
    "WHERE t.name ILIKE '%cristal%' OR t.name ILIKE '%moquegua%' "
    "ORDER BY t.name"
)).fetchall()

print("=== Specific teams ===")
for r in teams:
    print(f"  id={r[0]}  name={r[1]}  crest={'YES' if r[2] else 'NULL'}")
    if r[2]:
        print(f"    url={r[2]}")

# Check how many teams have NULL crest_url and have ESPN mapping
no_crest = db.execute(text(
    "SELECT t.id, t.name FROM teams t "
    "WHERE t.crest_url IS NULL "
    "ORDER BY t.name"
)).fetchall()

print(f"\n=== Teams WITHOUT crest_url: {len(no_crest)} ===")
for r in no_crest:
    print(f"  id={r[0]}  {r[1]}")

# Check if those teams have any external_id mapping
print(f"\n=== Checking ESPN mappings for teams without crest ===")
for r in no_crest[:10]:
    mapping = db.execute(text(
        "SELECT e.external_id, s.name FROM external_ids e "
        "JOIN sources s ON s.id = e.source_id "
        "WHERE e.entity_type = 'team' AND e.canonical_id = :tid"
    ), {"tid": r[0]}).fetchall()
    if mapping:
        print(f"  id={r[0]} {r[1]} -> mappings: {[(m[1], m[0]) for m in mapping]}")
    else:
        print(f"  id={r[0]} {r[1]} -> NO MAPPING")

if __name__ == "__main__":
    pass  # module-level code runs above
