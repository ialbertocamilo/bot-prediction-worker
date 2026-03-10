"""add attack and defense columns to team_ratings

Revision ID: c7d8e9f0a1b2
Revises: b5c6d7e8f901
Create Date: 2026-03-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('team_ratings', sa.Column('attack', sa.Float(), nullable=True))
    op.add_column('team_ratings', sa.Column('defense', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('team_ratings', 'defense')
    op.drop_column('team_ratings', 'attack')
