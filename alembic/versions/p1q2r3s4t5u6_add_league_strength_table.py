"""add league_strength table

Revision ID: p1q2r3s4t5u6
Revises: n0p1q2r3s4t5
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "p1q2r3s4t5u6"
down_revision = "n0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "league_strength",
        sa.Column("league_key", sa.String(length=50), nullable=False),
        sa.Column("coefficient", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("league_key"),
    )


def downgrade() -> None:
    op.drop_table("league_strength")
