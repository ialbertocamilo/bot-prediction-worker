"""Backfill crest_url for all teams that have an ESPN external ID mapping."""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import SessionLocal

ESPN_LOGO_TPL = "https://a.espncdn.com/i/teamlogos/soccer/500/{}.png"


def main() -> None:
    db = SessionLocal()
    try:
        # Find ESPN source
        row = db.execute(
            text("SELECT id FROM sources WHERE name = 'espn-scraper'")
        ).first()
        if not row:
            print("ESPN source not found in DB")
            return

        src_id = row[0]

        # Get all ESPN team mappings
        mappings = db.execute(
            text(
                "SELECT external_id, canonical_id FROM external_ids "
                "WHERE source_id = :src AND entity_type = 'team'"
            ),
            {"src": src_id},
        ).fetchall()

        print(f"Found {len(mappings)} ESPN team mappings")

        updated = 0
        for espn_id, team_id in mappings:
            crest_url = ESPN_LOGO_TPL.format(espn_id)
            result = db.execute(
                text(
                    "UPDATE teams SET crest_url = :url "
                    "WHERE id = :tid"
                ),
                {"url": crest_url, "tid": team_id},
            )
            if result.rowcount > 0:
                updated += 1

        db.commit()
        total = db.execute(text("SELECT count(*) FROM teams")).scalar()
        with_crest = db.execute(
            text("SELECT count(*) FROM teams WHERE crest_url IS NOT NULL")
        ).scalar()
        print(f"Updated: {updated} teams")
        print(f"Total teams: {total}, with crest: {with_crest}")
    finally:
        db.close()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    main()
