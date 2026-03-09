from __future__ import annotations

import os
from datetime import date, timedelta

from app.db.session import SessionLocal
from app.providers.factory import ProviderFactory
from app.services.ingest.match_ingest_service import MatchIngestService


def main() -> None:
    provider_name: str = os.getenv("ACTIVE_PROVIDER", "api-football")
    league_id_raw: str | None = os.getenv("DEFAULT_LEAGUE_ID")
    season_raw: str | None = os.getenv("DEFAULT_SEASON")

    if league_id_raw is None:
        raise RuntimeError("DEFAULT_LEAGUE_ID no está definida en el entorno")

    if season_raw is None:
        raise RuntimeError("DEFAULT_SEASON no está definida en el entorno")

    league_id: int = int(league_id_raw)
    season: int = int(season_raw)

    provider = ProviderFactory.create(provider_name)

    date_from_raw: str | None = os.getenv("SYNC_DATE_FROM")
    date_to_raw: str | None = os.getenv("SYNC_DATE_TO")
    days_ahead: int = int(os.getenv("SYNC_DAYS_AHEAD", "7"))

    date_from: date = date.fromisoformat(date_from_raw) if date_from_raw else date.today()
    date_to: date = date.fromisoformat(date_to_raw) if date_to_raw else date_from + timedelta(days=days_ahead)

    print(f"Proveedor: {provider_name}")
    print(f"Liga: {league_id} | Temporada: {season}")
    print(f"Rango: {date_from.isoformat()} → {date_to.isoformat()}")

    matches = provider.get_fixtures(
        league_id=league_id,
        season=season,
        date_from=date_from,
        date_to=date_to,
    )

    print(f"Partidos recibidos del proveedor: {len(matches)}")

    db = SessionLocal()
    try:
        ingest_service = MatchIngestService(db)
        match_ids = ingest_service.ingest_matches(matches=matches)
        db.commit()

        print(f"Se sincronizaron {len(match_ids)} partidos")
        print(match_ids)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()