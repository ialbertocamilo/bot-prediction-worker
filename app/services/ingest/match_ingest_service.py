from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.canonical import CanonicalMatch
from app.repositories.core.external_id_repository import ExternalIdRepository
from app.repositories.core.raw_record_repository import RawRecordRepository
from app.repositories.core.source_repository import SourceRepository
from app.repositories.football.league_repository import LeagueRepository
from app.repositories.football.match_repository import MatchRepository
from app.repositories.football.season_repository import SeasonRepository
from app.repositories.football.team_repository import TeamRepository
from app.repositories.football.venue_repository import VenueRepository


class MatchIngestService:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

        self.source_repo: SourceRepository = SourceRepository(db)
        self.external_id_repo: ExternalIdRepository = ExternalIdRepository(db)
        self.raw_record_repo: RawRecordRepository = RawRecordRepository(db)

        self.league_repo: LeagueRepository = LeagueRepository(db)
        self.season_repo: SeasonRepository = SeasonRepository(db)
        self.team_repo: TeamRepository = TeamRepository(db)
        self.venue_repo: VenueRepository = VenueRepository(db)
        self.match_repo: MatchRepository = MatchRepository(db)

    def ingest_matches(
        self,
        matches: list[CanonicalMatch],
        raw_payloads: list[dict[str, Any]] | None = None,
    ) -> list[int]:
        created_or_updated_match_ids: list[int] = []

        for index, canonical_match in enumerate(matches):
            raw_payload: dict[str, Any] | None = None
            if raw_payloads is not None and index < len(raw_payloads):
                raw_payload = raw_payloads[index]

            match_id: int = self.ingest_match(
                canonical_match=canonical_match,
                raw_payload=raw_payload,
            )
            created_or_updated_match_ids.append(match_id)

        return created_or_updated_match_ids

    def ingest_match(
        self,
        canonical_match: CanonicalMatch,
        raw_payload: dict[str, Any] | None = None,
    ) -> int:
        if canonical_match.source_ref is None:
            raise ValueError("CanonicalMatch.source_ref es obligatorio para ingestión")

        source = self.source_repo.get_or_create(
            name=canonical_match.source_ref.source_name,
            kind="api",
        )

        if raw_payload is not None:
            self.raw_record_repo.create(
                source_id=source.id,
                entity_type="match",
                external_id=canonical_match.source_ref.external_id,
                payload=raw_payload,
                fetched_at=canonical_match.source_ref.fetched_at or datetime.utcnow(),
            )

        league_id: int = self._resolve_league(canonical_match=canonical_match)
        season_id: int | None = self._resolve_season(
            league_id=league_id,
            canonical_match=canonical_match,
        )

        home_team_id: int = self._resolve_team(
            source_id=source.id,
            external_team_id=canonical_match.home_team_external_id,
            team_name=canonical_match.home_team_name,
        )

        away_team_id: int = self._resolve_team(
            source_id=source.id,
            external_team_id=canonical_match.away_team_external_id,
            team_name=canonical_match.away_team_name,
        )

        venue_id: int | None = None

        existing_mapping = self.external_id_repo.find_mapping(
            source_id=source.id,
            entity_type="match",
            external_id=canonical_match.source_ref.external_id,
        )

        if existing_mapping is not None:
            existing_match = self.match_repo.get_by_id(existing_mapping.canonical_id)
            if existing_match is None:
                raise RuntimeError(
                    "Existe external_id para match pero no existe el match canónico"
                )

            updated_match = self.match_repo.update(
                existing_match,
                status=canonical_match.status.value,
                venue_id=venue_id,
                home_goals=canonical_match.home_goals,
                away_goals=canonical_match.away_goals,
                ht_home_goals=canonical_match.ht_home_goals,
                ht_away_goals=canonical_match.ht_away_goals,
                round_value=canonical_match.round,
                referee=canonical_match.referee,
            )
            return updated_match.id

        existing_match_by_signature = self.match_repo.find_by_signature(
            league_id=league_id,
            utc_date=canonical_match.utc_date,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )

        if existing_match_by_signature is not None:
            updated_match = self.match_repo.update(
                existing_match_by_signature,
                status=canonical_match.status.value,
                venue_id=venue_id,
                home_goals=canonical_match.home_goals,
                away_goals=canonical_match.away_goals,
                ht_home_goals=canonical_match.ht_home_goals,
                ht_away_goals=canonical_match.ht_away_goals,
                round_value=canonical_match.round,
                referee=canonical_match.referee,
            )

            self.external_id_repo.get_or_create_mapping(
                source_id=source.id,
                entity_type="match",
                external_id=canonical_match.source_ref.external_id,
                canonical_id=updated_match.id,
            )
            return updated_match.id

        created_match = self.match_repo.create(
            league_id=league_id,
            season_id=season_id,
            venue_id=venue_id,
            utc_date=canonical_match.utc_date,
            status=canonical_match.status.value,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_goals=canonical_match.home_goals,
            away_goals=canonical_match.away_goals,
            ht_home_goals=canonical_match.ht_home_goals,
            ht_away_goals=canonical_match.ht_away_goals,
            round_value=canonical_match.round,
            referee=canonical_match.referee,
        )

        self.external_id_repo.create_mapping(
            source_id=source.id,
            entity_type="match",
            external_id=canonical_match.source_ref.external_id,
            canonical_id=created_match.id,
        )

        return created_match.id

    def _resolve_league(
        self,
        canonical_match: CanonicalMatch,
    ) -> int:
        league_name: str = canonical_match.league_name or "Unknown League"

        league = self.league_repo.get_or_create(
            name=league_name,
            country=None,
            level=None,
        )
        return league.id

    def _resolve_season(
        self,
        league_id: int,
        canonical_match: CanonicalMatch,
    ) -> int | None:
        if canonical_match.season_year is None:
            return None

        season = self.season_repo.get_or_create(
            league_id=league_id,
            year=canonical_match.season_year,
            start_date=None,
            end_date=None,
            is_current=None,
        )
        return season.id

    def _resolve_team(
        self,
        source_id: int,
        external_team_id: str | None,
        team_name: str,
    ) -> int:
        if external_team_id is not None:
            existing_mapping = self.external_id_repo.find_mapping(
                source_id=source_id,
                entity_type="team",
                external_id=external_team_id,
            )
            if existing_mapping is not None:
                return existing_mapping.canonical_id

        existing_team = self.team_repo.find_by_name_country(
            name=team_name,
            country=None,
        )

        if existing_team is None:
            existing_team = self.team_repo.create(
                name=team_name,
                short_name=None,
                country=None,
                founded_year=None,
            )

        if external_team_id is not None:
            self.external_id_repo.get_or_create_mapping(
                source_id=source_id,
                entity_type="team",
                external_id=external_team_id,
                canonical_id=existing_team.id,
            )

        return existing_team.id