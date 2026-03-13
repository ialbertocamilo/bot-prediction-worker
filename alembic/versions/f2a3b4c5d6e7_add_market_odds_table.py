"""add market_odds table

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-03-12
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_odds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bookmaker", sa.String(100), nullable=False),
        sa.Column("home_odds", sa.Float(), nullable=False),
        sa.Column("draw_odds", sa.Float(), nullable=False),
        sa.Column("away_odds", sa.Float(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_market_odds_match_bookmaker", "market_odds", ["match_id", "bookmaker"])
    op.create_index("ix_market_odds_match_id", "market_odds", ["match_id"])
    op.create_index("ix_market_odds_fetched_at", "market_odds", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_market_odds_fetched_at", table_name="market_odds")
    op.drop_index("ix_market_odds_match_id", table_name="market_odds")
    op.drop_constraint("uq_market_odds_match_bookmaker", "market_odds", type_="unique")
    op.drop_table("market_odds")
