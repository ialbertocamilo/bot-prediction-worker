"""merge_multiple_heads_fix

Revision ID: d1b1119ea7db
Revises: a37a9ad29c44, i5j6k7l8m9n0
Create Date: 2026-03-25 10:51:20.638035

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1b1119ea7db'
down_revision: Union[str, Sequence[str], None] = ('a37a9ad29c44', 'i5j6k7l8m9n0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
