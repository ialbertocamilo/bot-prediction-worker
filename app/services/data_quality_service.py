from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models.core.data_quality_report import DataQualityReport
from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.models.football.team import Team


@dataclass(slots=True)
class DataQualityIssue:
    issue_type: str
    entity_id: str | None
    description: str


class DataQualityService:
    ISSUE_TEAM_MISSING_TYPE = "TEAM_MISSING_TEAM_TYPE"
    ISSUE_NATIONAL_WITH_DOMESTIC = "NATIONAL_WITH_DOMESTIC_KEY"
    ISSUE_CLUB_MISSING_DOMESTIC = "CLUB_MISSING_DOMESTIC_KEY"
    ISSUE_MATCH_INVALID_FIELDS = "MATCH_INVALID_FIELDS"
    ISSUE_DUPLICATE_TEAM = "DUPLICATE_TEAM"
    ISSUE_DUPLICATE_LEAGUE = "DUPLICATE_LEAGUE"
    ISSUE_MIXED_MATCH = "MIXED_CLUB_VS_NATIONAL_MATCH"

    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self) -> list[DataQualityIssue]:
        issues: list[DataQualityIssue] = []
        issues.extend(self._teams_missing_team_type())
        issues.extend(self._national_teams_with_domestic_key())
        issues.extend(self._clubs_missing_domestic_key())
        issues.extend(self._matches_invalid_fields())
        issues.extend(self._duplicate_teams())
        issues.extend(self._duplicate_leagues())
        issues.extend(self._mixed_club_vs_national_matches())
        return issues

    def run_and_persist(self) -> int:
        issues = self.run()
        for it in issues:
            self.db.add(
                DataQualityReport(
                    issue_type=it.issue_type,
                    entity_id=it.entity_id,
                    description=it.description,
                )
            )
        self.db.flush()
        return len(issues)

    @staticmethod
    def _norm(text: str | None) -> str:
        if not text:
            return ""
        t = unicodedata.normalize("NFKD", text)
        t = "".join(ch for ch in t if not unicodedata.combining(ch))
        t = t.lower().strip()
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _teams_missing_team_type(self) -> list[DataQualityIssue]:
        stmt = select(Team).where(or_(Team.team_type.is_(None), Team.team_type == ""))
        teams = list(self.db.scalars(stmt).all())
        return [
            DataQualityIssue(
                issue_type=self.ISSUE_TEAM_MISSING_TYPE,
                entity_id=str(t.id),
                description=f"Team '{t.name}' has missing team_type",
            )
            for t in teams
        ]

    def _national_teams_with_domestic_key(self) -> list[DataQualityIssue]:
        stmt = select(Team).where(Team.team_type == "NATIONAL").where(Team.domestic_league_key.isnot(None))
        teams = list(self.db.scalars(stmt).all())
        return [
            DataQualityIssue(
                issue_type=self.ISSUE_NATIONAL_WITH_DOMESTIC,
                entity_id=str(t.id),
                description=f"National team '{t.name}' has domestic_league_key='{t.domestic_league_key}'",
            )
            for t in teams
        ]

    def _clubs_missing_domestic_key(self) -> list[DataQualityIssue]:
        stmt = select(Team).where(Team.team_type == "CLUB").where(Team.domestic_league_key.is_(None))
        teams = list(self.db.scalars(stmt).all())
        return [
            DataQualityIssue(
                issue_type=self.ISSUE_CLUB_MISSING_DOMESTIC,
                entity_id=str(t.id),
                description=f"Club team '{t.name}' is missing domestic_league_key",
            )
            for t in teams
        ]

    def _matches_invalid_fields(self) -> list[DataQualityIssue]:
        stmt = select(Match).where(
            or_(
                Match.home_team_id.is_(None),
                Match.away_team_id.is_(None),
                Match.utc_date.is_(None),
                Match.home_goals < 0,
                Match.away_goals < 0,
                Match.ht_home_goals < 0,
                Match.ht_away_goals < 0,
            )
        )
        matches = list(self.db.scalars(stmt).all())
        out: list[DataQualityIssue] = []
        for m in matches:
            parts: list[str] = []
            if m.home_team_id is None:
                parts.append("home_team_id NULL")
            if m.away_team_id is None:
                parts.append("away_team_id NULL")
            if m.utc_date is None:
                parts.append("utc_date NULL")
            if m.home_goals is not None and m.home_goals < 0:
                parts.append(f"home_goals={m.home_goals}")
            if m.away_goals is not None and m.away_goals < 0:
                parts.append(f"away_goals={m.away_goals}")
            if m.ht_home_goals is not None and m.ht_home_goals < 0:
                parts.append(f"ht_home_goals={m.ht_home_goals}")
            if m.ht_away_goals is not None and m.ht_away_goals < 0:
                parts.append(f"ht_away_goals={m.ht_away_goals}")
            desc = ", ".join(parts) if parts else "invalid match fields"
            out.append(
                DataQualityIssue(
                    issue_type=self.ISSUE_MATCH_INVALID_FIELDS,
                    entity_id=str(m.id),
                    description=f"Match {m.id} invalid: {desc}",
                )
            )
        return out

    def _duplicate_teams(self) -> list[DataQualityIssue]:
        teams = list(self.db.scalars(select(Team)).all())
        buckets: dict[tuple[str, str], list[Team]] = {}
        for t in teams:
            key = (self._norm(t.name), self._norm(t.country))
            buckets.setdefault(key, []).append(t)

        issues: list[DataQualityIssue] = []
        for (norm_name, norm_country), group in buckets.items():
            if not norm_name or len(group) < 2:
                continue
            group.sort(key=lambda x: x.id)
            ids = [str(t.id) for t in group]
            for t in group[1:]:
                issues.append(
                    DataQualityIssue(
                        issue_type=self.ISSUE_DUPLICATE_TEAM,
                        entity_id=str(t.id),
                        description=(
                            f"Duplicate team normalized=('{norm_name}','{norm_country}') "
                            f"ids={','.join(ids)}"
                        ),
                    )
                )
        return issues

    def _duplicate_leagues(self) -> list[DataQualityIssue]:
        leagues = list(self.db.scalars(select(League)).all())
        buckets: dict[tuple[str, str], list[League]] = {}
        for l in leagues:
            key = (self._norm(l.name), self._norm(l.country))
            buckets.setdefault(key, []).append(l)

        issues: list[DataQualityIssue] = []
        for (norm_name, norm_country), group in buckets.items():
            if not norm_name or len(group) < 2:
                continue
            group.sort(key=lambda x: x.id)
            ids = [str(l.id) for l in group]
            for l in group[1:]:
                issues.append(
                    DataQualityIssue(
                        issue_type=self.ISSUE_DUPLICATE_LEAGUE,
                        entity_id=str(l.id),
                        description=(
                            f"Duplicate league normalized=('{norm_name}','{norm_country}') "
                            f"ids={','.join(ids)}"
                        ),
                    )
                )
        return issues

    def _mixed_club_vs_national_matches(self) -> list[DataQualityIssue]:
        Home = aliased(Team)
        Away = aliased(Team)
        stmt = (
            select(Match.id, Home.team_type, Away.team_type)
            .join(Home, Home.id == Match.home_team_id)
            .join(Away, Away.id == Match.away_team_id)
            .where(or_(Home.team_type == "NATIONAL", Away.team_type == "NATIONAL"))
        )
        rows = list(self.db.execute(stmt))
        issues: list[DataQualityIssue] = []
        for match_id, home_type, away_type in rows:
            if home_type == away_type:
                continue
            issues.append(
                DataQualityIssue(
                    issue_type=self.ISSUE_MIXED_MATCH,
                    entity_id=str(match_id),
                    description=f"Match {match_id} is mixed types: home={home_type} away={away_type}",
                )
            )
        return issues

