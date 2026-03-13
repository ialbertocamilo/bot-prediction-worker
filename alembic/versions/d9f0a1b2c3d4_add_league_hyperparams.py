"""add league_hyperparams table

Revision ID: d9f0a1b2c3d4
Revises: c7d8e9f0a1b2
Create Date: 2026-03-12
"""
from alembic import op
import sqlalchemy as sa

revision = "d9f0a1b2c3d4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "league_hyperparams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("time_decay", sa.Float(), nullable=True),
        sa.Column("xg_reg_weight", sa.Float(), nullable=True),
        sa.Column("home_advantage", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.UniqueConstraint("league_id", name="uq_league_hyperparams_league"),
    )


def downgrade() -> None:
    op.drop_table("league_hyperparams")
