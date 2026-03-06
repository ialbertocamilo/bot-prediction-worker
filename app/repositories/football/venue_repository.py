from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.football.venue import Venue


class VenueRepository:
    def __init__(self, db: Session) -> None:
        self.db: Session = db

    def get_by_id(self, venue_id: int) -> Venue | None:
        return self.db.get(Venue, venue_id)

    def find_by_name_city(
        self,
        name: str,
        city: str | None = None,
    ) -> Venue | None:
        stmt = select(Venue).where(Venue.name == name)

        if city is None:
            stmt = stmt.where(Venue.city.is_(None))
        else:
            stmt = stmt.where(Venue.city == city)

        return self.db.scalar(stmt)

    def create(
        self,
        name: str,
        city: str | None = None,
        capacity: int | None = None,
    ) -> Venue:
        venue: Venue = Venue(
            name=name,
            city=city,
            capacity=capacity,
        )
        self.db.add(venue)
        self.db.flush()
        self.db.refresh(venue)
        return venue

    def get_or_create(
        self,
        name: str,
        city: str | None = None,
        capacity: int | None = None,
    ) -> Venue:
        venue: Venue | None = self.find_by_name_city(name=name, city=city)
        if venue is not None:
            return venue
        return self.create(name=name, city=city, capacity=capacity)