"""Backfill crests for teams missing ESPN mapping by searching ESPN API."""
import sys, io, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.request
import urllib.parse
import json
from sqlalchemy import text
from app.db.session import SessionLocal

ESPN_SEARCH = "https://site.web.api.espn.com/apis/common/v3/search?query={}&limit=5&type=team&sport=soccer"
ESPN_LOGO_TPL = "https://a.espncdn.com/i/teamlogos/soccer/500/{}.png"


def search_espn_team(name: str) -> str | None:
    """Search ESPN for a team and return logo URL if found."""
    url = ESPN_SEARCH.format(urllib.parse.quote(name))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        items = data.get("items", [])
        for item in items:
            logos = item.get("logos", [])
            if logos:
                href = logos[0].get("href")
                if href:
                    return href
    except Exception:
        pass
    return None


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, name FROM teams WHERE crest_url IS NULL ORDER BY name"
        )).fetchall()
        print(f"Teams without crest: {len(rows)}")

        updated = 0
        for team_id, team_name in rows:
            logo = search_espn_team(team_name)
            if logo:
                db.execute(
                    text("UPDATE teams SET crest_url = :url WHERE id = :tid"),
                    {"url": logo, "tid": team_id},
                )
                updated += 1
                print(f"  OK  {team_name} -> {logo}")
            else:
                # Try shorter name (first two words)
                short = " ".join(team_name.split()[:2])
                if short != team_name:
                    logo = search_espn_team(short)
                    if logo:
                        db.execute(
                            text("UPDATE teams SET crest_url = :url WHERE id = :tid"),
                            {"url": logo, "tid": team_id},
                        )
                        updated += 1
                        print(f"  OK  {team_name} (via '{short}') -> {logo}")
                    else:
                        print(f"  MISS {team_name}")
                else:
                    print(f"  MISS {team_name}")
            time.sleep(0.5)  # rate limit

        db.commit()
        remaining = db.execute(text(
            "SELECT count(*) FROM teams WHERE crest_url IS NULL"
        )).scalar()
        print(f"\nUpdated: {updated}, Still missing: {remaining}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
