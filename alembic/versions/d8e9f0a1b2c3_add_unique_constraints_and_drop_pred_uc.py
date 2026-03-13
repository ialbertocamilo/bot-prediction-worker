"""add unique constraints to teams, match_events, players and drop prediction UC

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-03-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_teams_name_country", "teams", ["name", "country"],
    )
    op.create_unique_constraint(
        "uq_match_events_dedup",
        "match_events",
        ["match_id", "minute", "team_id", "event_type", "player_name"],
    )
    op.create_unique_constraint(
        "uq_players_name_team", "players", ["name", "team_id"],
    )
    op.drop_constraint("uq_predictions_match_model", "predictions", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_predictions_match_model", "predictions", ["match_id", "model_id"],
    )
    op.drop_constraint("uq_players_name_team", "players", type_="unique")
    op.drop_constraint("uq_match_events_dedup", "match_events", type_="unique")
    op.drop_constraint("uq_teams_name_country", "teams", type_="unique")
