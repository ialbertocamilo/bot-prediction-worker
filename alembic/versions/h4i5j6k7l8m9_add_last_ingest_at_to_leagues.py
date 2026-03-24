"""add last_ingest_at to leagues

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8
Create Date: 2026-03-24
"""
import sqlalchemy as sa
from alembic import op

revision = "h4i5j6k7l8m9"
down_revision = "g3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leagues",
        sa.Column("last_ingest_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leagues", "last_ingest_at")
