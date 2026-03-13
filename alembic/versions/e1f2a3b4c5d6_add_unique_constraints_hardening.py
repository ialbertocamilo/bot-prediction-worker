"""add unique constraints for production hardening

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-03-15
"""
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Match: prevent duplicate fixtures
    op.create_unique_constraint(
        "uq_matches_signature",
        "matches",
        ["league_id", "utc_date", "home_team_id", "away_team_id"],
    )

    # Prediction: one prediction per match per model
    op.create_unique_constraint(
        "uq_predictions_match_model",
        "predictions",
        ["match_id", "model_id"],
    )

    # TeamRating: one snapshot per (model, team, match)
    op.create_unique_constraint(
        "uq_team_ratings_model_team_match",
        "team_ratings",
        ["model_id", "team_id", "as_of_match_id"],
    )

    # raw_records: index for TTL purge queries
    op.create_index(
        "ix_raw_records_fetched_at",
        "raw_records",
        ["fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_records_fetched_at", table_name="raw_records")
    op.drop_constraint("uq_team_ratings_model_team_match", "team_ratings", type_="unique")
    op.drop_constraint("uq_predictions_match_model", "predictions", type_="unique")
    op.drop_constraint("uq_matches_signature", "matches", type_="unique")
