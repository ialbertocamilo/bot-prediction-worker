"""add league_status_date indexes

Revision ID: g3h4i5j6k7l8
Revises: f2a3b4c5d6e7
Create Date: 2026-03-13
"""
from alembic import op

revision = "g3h4i5j6k7l8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_matches_league_status_date",
        "matches",
        ["league_id", "status", "utc_date"],
    )
    op.create_index(
        "ix_matches_status_date",
        "matches",
        ["status", "utc_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_matches_status_date", table_name="matches")
    op.drop_index("ix_matches_league_status_date", table_name="matches")
