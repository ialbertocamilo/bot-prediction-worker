"""add over under 1_5 and 3_5 to predictions

Revision ID: b5c6d7e8f901
Revises: a3b4c5d6e7f8
Create Date: 2026-03-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b5c6d7e8f901'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('predictions', sa.Column('p_over_1_5', sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('p_under_1_5', sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('p_over_3_5', sa.Float(), nullable=True))
    op.add_column('predictions', sa.Column('p_under_3_5', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('predictions', 'p_under_3_5')
    op.drop_column('predictions', 'p_over_3_5')
    op.drop_column('predictions', 'p_under_1_5')
    op.drop_column('predictions', 'p_over_1_5')
