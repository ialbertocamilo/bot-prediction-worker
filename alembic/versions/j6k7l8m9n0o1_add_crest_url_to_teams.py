"""add crest_url to teams

Revision ID: j6k7l8m9n0o1
Revises: d1b1119ea7db
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = "j6k7l8m9n0o1"
down_revision = "d1b1119ea7db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("crest_url", sa.String(400), nullable=True))


def downgrade() -> None:
    op.drop_column("teams", "crest_url")
