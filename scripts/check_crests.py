"""Quick check: verify crest URLs stored in DB and test one."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from app.db.session import SessionLocal

db = SessionLocal()

# Sample crest URLs
rows = db.execute(text(
    "SELECT t.id, t.name, t.crest_url FROM teams t "
    "WHERE t.crest_url IS NOT NULL LIMIT 5"
)).fetchall()

print("=== Sample crest_url values ===")
for r in rows:
    print(f"  id={r[0]}  name={r[1]}")
    print(f"    url={r[2]}")

# Count
total = db.execute(text("SELECT count(*) FROM teams")).scalar()
with_crest = db.execute(text("SELECT count(*) FROM teams WHERE crest_url IS NOT NULL")).scalar()
print(f"\nTotal teams: {total}, with crest: {with_crest}, without: {total - with_crest}")

# Test if a URL actually loads
if rows:
    import urllib.request
    url = rows[0][2]
    print(f"\n=== Testing URL: {url} ===")
    try:
        req = urllib.request.Request(url, method="HEAD")
        resp = urllib.request.urlopen(req, timeout=10)
        print(f"  Status: {resp.status}")
        print(f"  Content-Type: {resp.headers.get('Content-Type')}")
        print(f"  Content-Length: {resp.headers.get('Content-Length')}")
    except Exception as e:
        print(f"  FAILED: {e}")

db.close()
