from __future__ import annotations

from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models.core.data_quality_report import DataQualityReport
from app.db.models.football.league import League
from app.db.models.football.match import Match
from app.db.models.football.season import Season
from app.db.models.football.team import Team
from app.db.models.football.venue import Venue
from app.services.data_quality_service import DataQualityService


class DataQualityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            bind=engine,
            tables=[
                Team.__table__,
                League.__table__,
                Season.__table__,
                Venue.__table__,
                Match.__table__,
                DataQualityReport.__table__,
            ],
        )
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def test_inserts_issues_for_bad_data(self) -> None:
        db = self.Session()
        try:
            l1 = League(name="Copa América", country=None, level=None)
            l2 = League(name="Copa America", country=None, level=None)
            db.add_all([l1, l2])
            db.flush()

            t_missing_type = Team(name="Foo FC", country="Peru", team_type="", domestic_league_key="liga1-peru")
            t_national_bad = Team(name="Brazil", country="Brazil", team_type="NATIONAL", domestic_league_key="brasileirao")
            t_club_missing_domestic = Team(name="Bar FC", country="Peru", team_type="CLUB", domestic_league_key=None)
            t_dup_1 = Team(name="São Paulo", country="Brazil", team_type="CLUB", domestic_league_key="brasileirao")
            t_dup_2 = Team(name="Sao Paulo", country="Brazil", team_type="CLUB", domestic_league_key="brasileirao")
            db.add_all([t_missing_type, t_national_bad, t_club_missing_domestic, t_dup_1, t_dup_2])
            db.flush()

            m_negative = Match(
                league_id=l1.id,
                season_id=None,
                venue_id=None,
                utc_date=datetime(2026, 6, 30, tzinfo=timezone.utc),
                status="FINISHED",
                home_team_id=t_dup_1.id,
                away_team_id=t_dup_2.id,
                home_goals=-1,
                away_goals=0,
                ht_home_goals=None,
                ht_away_goals=None,
                round=None,
                referee=None,
                clock_display=None,
                is_finished=True,
                processed_for_training=False,
                updated_at=None,
            )
            m_mixed = Match(
                league_id=l1.id,
                season_id=None,
                venue_id=None,
                utc_date=datetime(2026, 6, 29, tzinfo=timezone.utc),
                status="SCHEDULED",
                home_team_id=t_club_missing_domestic.id,
                away_team_id=t_national_bad.id,
                home_goals=None,
                away_goals=None,
                ht_home_goals=None,
                ht_away_goals=None,
                round=None,
                referee=None,
                clock_display=None,
                is_finished=False,
                processed_for_training=False,
                updated_at=None,
            )
            db.add_all([m_negative, m_mixed])
            db.flush()

            created = DataQualityService(db).run_and_persist()
            db.commit()

            self.assertGreaterEqual(created, 6)

            rows = db.query(DataQualityReport).all()
            types = {r.issue_type for r in rows}

            self.assertIn(DataQualityService.ISSUE_TEAM_MISSING_TYPE, types)
            self.assertIn(DataQualityService.ISSUE_NATIONAL_WITH_DOMESTIC, types)
            self.assertIn(DataQualityService.ISSUE_CLUB_MISSING_DOMESTIC, types)
            self.assertIn(DataQualityService.ISSUE_MATCH_INVALID_FIELDS, types)
            self.assertIn(DataQualityService.ISSUE_DUPLICATE_TEAM, types)
            self.assertIn(DataQualityService.ISSUE_DUPLICATE_LEAGUE, types)
            self.assertIn(DataQualityService.ISSUE_MIXED_MATCH, types)
        finally:
            db.close()
