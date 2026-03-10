"""
Genera partidos de prueba SCHEDULED usando equipos existentes en la DB.
Esto simula lo que llegaría de la API cuando hay fixtures futuros.
"""
import random
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.db.models.football.match import Match
from sqlalchemy import text

db = SessionLocal()

# Get league and teams
league_id = db.execute(text("SELECT id FROM leagues LIMIT 1")).scalar()
teams = db.execute(text("SELECT id FROM teams ORDER BY id")).fetchall()
team_ids = [t[0] for t in teams]

if len(team_ids) < 4:
    print("No hay suficientes equipos para generar fixtures.")
    exit(1)

# Get season
season_id = db.execute(text("SELECT id FROM seasons WHERE league_id = :lid ORDER BY year DESC LIMIT 1"), {"lid": league_id}).scalar()

# Generate 10 upcoming matches over the next 7 days
now = datetime.now(timezone.utc)
created = 0
random.seed(42)
random.shuffle(team_ids)

for i in range(0, min(len(team_ids), 10) * 2, 2):
    if i + 1 >= len(team_ids):
        break
    home_id = team_ids[i % len(team_ids)]
    away_id = team_ids[(i + 1) % len(team_ids)]
    if home_id == away_id:
        continue

    match_date = now + timedelta(days=random.randint(1, 7), hours=random.choice([14, 16, 18, 20]))

    # Check if exists
    existing = db.execute(
        text("SELECT id FROM matches WHERE league_id = :lid AND home_team_id = :hid AND away_team_id = :aid AND utc_date = :dt"),
        {"lid": league_id, "hid": home_id, "aid": away_id, "dt": match_date}
    ).scalar()
    if existing:
        continue

    match = Match(
        league_id=league_id,
        season_id=season_id,
        utc_date=match_date,
        status="SCHEDULED",
        home_team_id=home_id,
        away_team_id=away_id,
        round=f"Jornada {20 + i // 2}",
    )
    db.add(match)
    created += 1

db.commit()
print(f"Creados {created} partidos SCHEDULED de prueba")

# Verify
rows = db.execute(text("SELECT m.id, m.utc_date, m.status, h.name, a.name FROM matches m JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id WHERE m.status = 'SCHEDULED' ORDER BY m.utc_date")).fetchall()
for r in rows:
    print(f"  ID={r[0]} {r[1]} {r[3]} vs {r[4]} [{r[2]}]")

db.close()
