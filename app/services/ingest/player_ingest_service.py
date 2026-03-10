from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.canonical import CanonicalPlayer
from app.repositories.core.external_id_repository import ExternalIdRepository
from app.repositories.core.source_repository import SourceRepository
from app.repositories.football.player_repository import PlayerRepository
from app.repositories.football.team_repository import TeamRepository


class PlayerIngestService:
    def __init__(self, db: Session) -> None:
        self.db: Session = db
        self.source_repo = SourceRepository(db)
        self.external_id_repo = ExternalIdRepository(db)
        self.player_repo = PlayerRepository(db)
        self.team_repo = TeamRepository(db)

    def ingest_players(self, players: list[CanonicalPlayer]) -> list[int]:
        ids: list[int] = []
        for cp in players:
            ids.append(self.ingest_player(cp))
        return ids

    def ingest_player(self, canonical: CanonicalPlayer) -> int:
        if canonical.source_ref is None:
            raise ValueError("CanonicalPlayer.source_ref es obligatorio para ingestión")

        kind = "scraper" if "scraper" in canonical.source_ref.source_name else "api"
        source = self.source_repo.get_or_create(
            name=canonical.source_ref.source_name,
            kind=kind,
        )

        team_id: int | None = None
        if canonical.team_name is not None:
            team = self.team_repo.get_or_create(name=canonical.team_name)
            team_id = team.id

        existing_mapping = self.external_id_repo.find_mapping(
            source_id=source.id,
            entity_type="player",
            external_id=canonical.source_ref.external_id,
        )

        fields = dict(
            date_of_birth=canonical.date_of_birth,
            nationality=canonical.nationality,
            position=canonical.position.value,
            height_cm=canonical.height_cm,
            weight_kg=canonical.weight_kg,
            foot=canonical.foot.value,
            team_id=team_id,
            jersey_number=canonical.jersey_number,
            market_value_eur=canonical.market_value_eur,
            contract_until=canonical.contract_until,
        )

        if existing_mapping is not None:
            player = self.player_repo.get_by_id(existing_mapping.canonical_id)
            if player is None:
                raise RuntimeError(
                    "Existe external_id para player pero no existe el player canónico"
                )
            self.player_repo.update(player, **fields)
            return player.id

        existing_player = self.player_repo.find_by_name_team(
            name=canonical.name,
            team_id=team_id,
        )

        if existing_player is not None:
            self.player_repo.update(existing_player, **fields)
            self.external_id_repo.create_mapping(
                source_id=source.id,
                entity_type="player",
                external_id=canonical.source_ref.external_id,
                canonical_id=existing_player.id,
            )
            return existing_player.id

        created = self.player_repo.create(name=canonical.name, **fields)

        self.external_id_repo.create_mapping(
            source_id=source.id,
            entity_type="player",
            external_id=canonical.source_ref.external_id,
            canonical_id=created.id,
        )
        return created.id
