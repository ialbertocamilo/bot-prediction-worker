"""add clock_display to matches

Revision ID: l8m9n0o1p2q3
Revises: 0c5c44c73deb
Create Date: 2026-03-31
"""
import sqlalchemy as sa
from alembic import op

revision = "l8m9n0o1p2q3"
down_revision = "0c5c44c73deb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("clock_display", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matches", "clock_display")
