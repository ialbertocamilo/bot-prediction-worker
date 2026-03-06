from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prediction.model import Model


class ModelRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, model_id: int) -> Model | None:
        return self.db.get(Model, model_id)

    def get_by_name(self, name: str) -> Model | None:
        stmt = select(Model).where(Model.name == name)
        return self.db.scalar(stmt)

    def create(
        self,
        name: str,
        description: str | None = None,
    ) -> Model:
        model: Model = Model(name=name, description=description)
        self.db.add(model)
        self.db.flush()
        self.db.refresh(model)
        return model

    def get_or_create(
        self,
        name: str,
        description: str | None = None,
    ) -> Model:
        model: Model | None = self.get_by_name(name)
        if model is not None:
            return model
        return self.create(name=name, description=description)

    def list_all(self) -> list[Model]:
        stmt = select(Model).order_by(Model.id.asc())
        return list(self.db.scalars(stmt).all())