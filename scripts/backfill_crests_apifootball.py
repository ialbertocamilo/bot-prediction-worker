"""Backfill team crests using the api-football provider.

Real solution: queries api-football API by external_id for teams missing crests.
This covers teams that ESPN doesn't know but api-football does.
"""
import sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.providers.api_football.client import ApiFootballClient


def main() -> None:
    db = SessionLocal()
    client = ApiFootballClient()

    # Find all teams without crest that DO have an api-football mapping
    rows = db.execute(text(
        "SELECT t.id, t.name, ei.external_id "
        "FROM teams t "
        "JOIN external_ids ei ON ei.canonical_id = t.id AND ei.entity_type = 'team' "
        "JOIN sources s ON s.id = ei.source_id AND s.name = 'api-football' "
        "WHERE t.crest_url IS NULL "
        "ORDER BY t.name"
    )).fetchall()

    print(f"Teams without crest that have api-football mapping: {len(rows)}")

    updated = 0
    for team_id, team_name, ext_id in rows:
        try:
            resp = client._get("/teams", params={"id": int(ext_id)})
            items = resp.get("response", [])
            if items:
                logo = items[0].get("team", {}).get("logo")
                if logo:
                    db.execute(
                        text("UPDATE teams SET crest_url = :url WHERE id = :tid"),
                        {"url": logo, "tid": team_id},
                    )
                    updated += 1
                    print(f"  OK  {team_name} (ext={ext_id}) -> {logo}")
                else:
                    print(f"  NO-LOGO {team_name} (ext={ext_id})")
            else:
                print(f"  NOT-FOUND {team_name} (ext={ext_id})")
        except Exception as e:
            print(f"  ERROR {team_name}: {e}")

    db.commit()

    # Now check ALL teams still missing (regardless of mapping)
    still_missing = db.execute(text(
        "SELECT t.id, t.name "
        "FROM teams t "
        "WHERE t.crest_url IS NULL "
        "ORDER BY t.name"
    )).fetchall()

    print(f"\nUpdated via external_id: {updated}")

    # Phase 2: search api-football by name for teams without mapping
    if still_missing:
        print(f"\n=== Phase 2: Search api-football by name ({len(still_missing)} teams) ===")
        import time
        for tid, name in still_missing:
            search_names = [name]
            # Add short versions
            parts = name.split()
            if len(parts) > 2:
                search_names.append(" ".join(parts[:2]))
                search_names.append(" ".join(parts[1:]))

            found = False
            for sname in search_names:
                try:
                    resp = client._get("/teams", params={"search": sname})
                    items = resp.get("response", [])
                    if items:
                        logo = items[0].get("team", {}).get("logo")
                        api_name = items[0].get("team", {}).get("name", "?")
                        if logo:
                            db.execute(
                                text("UPDATE teams SET crest_url = :url WHERE id = :tid"),
                                {"url": logo, "tid": tid},
                            )
                            updated += 1
                            print(f"  OK  {name} -> {api_name} -> {logo}")
                            found = True
                            break
                except Exception as e:
                    print(f"  ERROR {name} ({sname}): {e}")
                time.sleep(0.3)

            if not found:
                print(f"  MISS {name}")

        db.commit()

    # Final stats
    still_missing = db.execute(text(
        "SELECT t.id, t.name FROM teams t WHERE t.crest_url IS NULL ORDER BY t.name"
    )).fetchall()
    print(f"\nStill missing: {len(still_missing)}")
    if still_missing:
        for tid, name in still_missing:
            print(f"  id={tid}: {name}")

    total = db.execute(text("SELECT count(*) FROM teams")).scalar()
    with_crest = db.execute(text("SELECT count(*) FROM teams WHERE crest_url IS NOT NULL")).scalar()
    print(f"\nTotal: {total}, With crest: {with_crest} ({100*with_crest/total:.1f}%)")
    db.close()


if __name__ == "__main__":
    main()
