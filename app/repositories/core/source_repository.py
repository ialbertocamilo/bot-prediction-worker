from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.core.source import Source


class SourceRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, source_id: int) -> Source | None:
        return self.db.get(Source, source_id)

    def get_by_name(self, name: str) -> Source | None:
        stmt = select(Source).where(Source.name == name)
        return self.db.scalar(stmt)

    def create(self, name: str, kind: str) -> Source:
        source: Source = Source(name=name, kind=kind)
        self.db.add(source)
        self.db.flush()
        self.db.refresh(source)
        return source

    def get_or_create(self, name: str, kind: str) -> Source:
        source: Source | None = self.get_by_name(name)
        if source is not None:
            return source
        return self.create(name=name, kind=kind)

    def list_all(self) -> list[Source]:
        stmt = select(Source).order_by(Source.id.asc())
        return list(self.db.scalars(stmt).all())