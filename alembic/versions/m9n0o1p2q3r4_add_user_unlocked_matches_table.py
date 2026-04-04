"""add user_unlocked_matches table

Revision ID: m9n0o1p2q3r4
Revises: l8m9n0o1p2q3
Create Date: 2026-04-03
"""
import sqlalchemy as sa
from alembic import op

revision = "m9n0o1p2q3r4"
down_revision = "l8m9n0o1p2q3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_unlocked_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("match_id", sa.Integer(), nullable=False, index=True),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_id", "match_id", name="uq_user_match_unlock"),
    )


def downgrade() -> None:
    op.drop_table("user_unlocked_matches")
