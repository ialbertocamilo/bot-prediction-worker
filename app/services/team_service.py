from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models.football.league import League
from app.db.models.football.team import Team
from app.repositories.football.league_repository import LeagueRepository
from app.repositories.football.match_repository import MatchRepository
from app.repositories.football.team_repository import TeamRepository

# Consider a team "active" if it has matches in the last 365 days or scheduled
_ACTIVE_DAYS = 365


class TeamService:
    def __init__(self, db: Session) -> None:
        self._teams = TeamRepository(db)
        self._matches = MatchRepository(db)
        self._leagues = LeagueRepository(db)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        teams: list[Team] = self._teams.search_by_name(query, limit=limit)
        return [
            {
                "team_id": t.id,
                "name": t.name,
                "country": t.country,
            }
            for t in teams
        ]

    def team_matches(
        self,
        team_id: int,
        status: str | None = None,
        limit: int = 50,
    ) -> dict:
        team: Team | None = self._teams.get_by_id(team_id)
        if team is None:
            return {}

        matches = self._matches.list_by_team(
            team_id, status=status, limit=limit,
        )

        items: list[dict] = []
        for m in matches:
            league: League | None = self._leagues.get_by_id(m.league_id)
            items.append({
                "match_id": m.id,
                "utc_date": m.utc_date.isoformat() if m.utc_date else None,
                "status": m.status,
                "round": m.round,
                "home_team": {
                    "team_id": m.home_team_id,
                    "name": m.home_team.name if m.home_team else None,
                },
                "away_team": {
                    "team_id": m.away_team_id,
                    "name": m.away_team.name if m.away_team else None,
                },
                "score": {
                    "home": m.home_goals,
                    "away": m.away_goals,
                } if m.home_goals is not None else None,
                "competition": {
                    "league_id": league.id,
                    "name": league.name,
                    "country": league.country,
                } if league else None,
            })

        return {
            "team_id": team.id,
            "team_name": team.name,
            "total": len(items),
            "matches": items,
        }

    def active_competitions(self, team_id: int) -> dict:
        team: Team | None = self._teams.get_by_id(team_id)
        if team is None:
            return {}

        cutoff = datetime.now(timezone.utc) - timedelta(days=_ACTIVE_DAYS)
        league_ids = self._matches.distinct_league_ids_for_team(
            team_id, cutoff=cutoff,
        )

        competitions: list[dict] = []
        for lid in sorted(league_ids):
            league: League | None = self._leagues.get_by_id(lid)
            if league is not None:
                competitions.append({
                    "league_id": league.id,
                    "name": league.name,
                    "country": league.country,
                })

        return {
            "team_id": team.id,
            "team_name": team.name,
            "active_competitions": competitions,
        }
