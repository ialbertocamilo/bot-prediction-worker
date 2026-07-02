"""add team_type and national_team_key to teams

Revision ID: n0p1q2r3s4t5
Revises: m9n0o1p2q3r4
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "n0p1q2r3s4t5"
down_revision = "m9n0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "team_type",
            sa.String(length=20),
            nullable=False,
            server_default="CLUB",
        ),
    )
    op.add_column(
        "teams",
        sa.Column("national_team_key", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("teams", "national_team_key")
    op.drop_column("teams", "team_type")
