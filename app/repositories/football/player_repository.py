from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.player import Player


class PlayerRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, player_id: int) -> Player | None:
        return self.db.get(Player, player_id)

    def find_by_name_team(
        self,
        name: str,
        team_id: int | None = None,
    ) -> Player | None:
        stmt = select(Player).where(Player.name == name)
        if team_id is not None:
            stmt = stmt.where(Player.team_id == team_id)
        return self.db.scalar(stmt)

    def create(
        self,
        name: str,
        date_of_birth: date | None = None,
        nationality: str | None = None,
        position: str = "UNKNOWN",
        height_cm: int | None = None,
        weight_kg: int | None = None,
        foot: str = "UNKNOWN",
        team_id: int | None = None,
        jersey_number: int | None = None,
        market_value_eur: int | None = None,
        contract_until: date | None = None,
    ) -> Player:
        player = Player(
            name=name,
            date_of_birth=date_of_birth,
            nationality=nationality,
            position=position,
            height_cm=height_cm,
            weight_kg=weight_kg,
            foot=foot,
            team_id=team_id,
            jersey_number=jersey_number,
            market_value_eur=market_value_eur,
            contract_until=contract_until,
        )
        self.db.add(player)
        self.db.flush()
        self.db.refresh(player)
        return player

    def update(
        self,
        player: Player,
        **kwargs: object,
    ) -> Player:
        for key, value in kwargs.items():
            if hasattr(player, key):
                setattr(player, key, value)
        self.db.flush()
        self.db.refresh(player)
        return player

    def get_or_create(
        self,
        name: str,
        team_id: int | None = None,
        **kwargs: object,
    ) -> Player:
        existing = self.find_by_name_team(name=name, team_id=team_id)
        if existing is not None:
            return existing
        return self.create(name=name, team_id=team_id, **kwargs)

    def list_by_team(self, team_id: int) -> list[Player]:
        stmt = select(Player).where(Player.team_id == team_id).order_by(Player.name.asc())
        return list(self.db.scalars(stmt).all())
