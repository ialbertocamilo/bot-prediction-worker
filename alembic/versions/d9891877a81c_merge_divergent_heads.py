"""merge_divergent_heads

Revision ID: d9891877a81c
Revises: 46ea4f81fc01, h4i5j6k7l8m9
Create Date: 2026-03-24 12:13:33.995373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9891877a81c'
down_revision: Union[str, Sequence[str], None] = ('46ea4f81fc01', 'h4i5j6k7l8m9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
