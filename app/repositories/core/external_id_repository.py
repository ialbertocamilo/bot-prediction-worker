from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.external_id import ExternalId


class ExternalIdRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, external_id_pk: int) -> ExternalId | None:
        return self.db.get(ExternalId, external_id_pk)

    def find_mapping(
        self,
        source_id: int,
        entity_type: str,
        external_id: str,
    ) -> ExternalId | None:
        stmt = (
            select(ExternalId)
            .where(ExternalId.source_id == source_id)
            .where(ExternalId.entity_type == entity_type)
            .where(ExternalId.external_id == external_id)
        )
        return self.db.scalar(stmt)

    def list_by_canonical(
        self,
        entity_type: str,
        canonical_id: int,
    ) -> list[ExternalId]:
        stmt = (
            select(ExternalId)
            .where(ExternalId.entity_type == entity_type)
            .where(ExternalId.canonical_id == canonical_id)
            .order_by(ExternalId.id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def create_mapping(
        self,
        source_id: int,
        entity_type: str,
        external_id: str,
        canonical_id: int,
    ) -> ExternalId:
        mapping: ExternalId = ExternalId(
            source_id=source_id,
            entity_type=entity_type,
            external_id=external_id,
            canonical_id=canonical_id,
        )
        self.db.add(mapping)
        self.db.flush()
        self.db.refresh(mapping)
        return mapping

    def get_or_create_mapping(
        self,
        source_id: int,
        entity_type: str,
        external_id: str,
        canonical_id: int,
    ) -> ExternalId:
        existing: ExternalId | None = self.find_mapping(
            source_id=source_id,
            entity_type=entity_type,
            external_id=external_id,
        )
        if existing is not None:
            return existing

        return self.create_mapping(
            source_id=source_id,
            entity_type=entity_type,
            external_id=external_id,
            canonical_id=canonical_id,
        )