"""add is_finished and processed_for_training to matches

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-03-25
"""
import sqlalchemy as sa
from alembic import op

revision = "i5j6k7l8m9n0"
down_revision = "h4i5j6k7l8m9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column(
            "is_finished",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "matches",
        sa.Column(
            "processed_for_training",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Backfill: mark existing FINISHED matches
    op.execute("UPDATE matches SET is_finished = true WHERE status = 'FINISHED'")

    op.create_index("ix_matches_is_finished", "matches", ["is_finished"])
    op.create_index(
        "ix_matches_processed_for_training", "matches", ["processed_for_training"]
    )


def downgrade() -> None:
    op.drop_index("ix_matches_processed_for_training", table_name="matches")
    op.drop_index("ix_matches_is_finished", table_name="matches")
    op.drop_column("matches", "processed_for_training")
    op.drop_column("matches", "is_finished")
