from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.canonical import CanonicalMatchStats
from app.repositories.core.external_id_repository import ExternalIdRepository
from app.repositories.core.source_repository import SourceRepository
from app.repositories.football.match_stats_repository import MatchStatsRepository
from app.repositories.football.team_repository import TeamRepository


class MatchStatsIngestService:
    def __init__(self, db: Session) -> None:
        self.db: Session = db
        self.source_repo = SourceRepository(db)
        self.external_id_repo = ExternalIdRepository(db)
        self.stats_repo = MatchStatsRepository(db)
        self.team_repo = TeamRepository(db)

    def ingest_match_stats(
        self,
        stats_list: list[CanonicalMatchStats],
        source_match_id_to_db_id: dict[str, int] | None = None,
    ) -> list[int]:
        """Ingest a list of canonical match stats.

        Args:
            stats_list: Canonical stats to ingest.
            source_match_id_to_db_id: Optional mapping from match external_id
                to the internal DB match.id.  If not provided, the service
                will attempt to find the match via ExternalId.
        """
        ids: list[int] = []
        for cs in stats_list:
            stats_id = self.ingest_stats(
                canonical=cs,
                source_match_id_to_db_id=source_match_id_to_db_id,
            )
            if stats_id is not None:
                ids.append(stats_id)
        return ids

    def ingest_stats(
        self,
        canonical: CanonicalMatchStats,
        source_match_id_to_db_id: dict[str, int] | None = None,
    ) -> int | None:
        if canonical.source_ref is None:
            raise ValueError("CanonicalMatchStats.source_ref es obligatorio para ingestión")

        kind = "scraper" if "scraper" in canonical.source_ref.source_name else "api"
        source = self.source_repo.get_or_create(
            name=canonical.source_ref.source_name,
            kind=kind,
        )

        # Resolve the internal match id
        match_id: int | None = None
        if source_match_id_to_db_id and canonical.match_external_id in source_match_id_to_db_id:
            match_id = source_match_id_to_db_id[canonical.match_external_id]
        else:
            mapping = self.external_id_repo.find_mapping(
                source_id=source.id,
                entity_type="match",
                external_id=canonical.match_external_id,
            )
            if mapping is not None:
                match_id = mapping.canonical_id

        if match_id is None:
            return None  # skip — match not yet ingested

        # Resolve team
        team = self.team_repo.find_by_name_country(name=canonical.team_name)
        if team is None:
            team = self.team_repo.create(name=canonical.team_name)
        team_id = team.id

        stat_fields = dict(
            possession_pct=canonical.possession_pct,
            shots=canonical.shots,
            shots_on_target=canonical.shots_on_target,
            xg=canonical.xg,
            xga=canonical.xga,
            corners=canonical.corners,
            fouls=canonical.fouls,
            offsides=canonical.offsides,
            yellow_cards=canonical.yellow_cards,
            red_cards=canonical.red_cards,
            passes=canonical.passes,
            pass_accuracy_pct=canonical.pass_accuracy_pct,
        )

        stats = self.stats_repo.upsert(
            match_id=match_id,
            team_id=team_id,
            **stat_fields,
        )
        return stats.id
