"""add_currency_to_payments

Revision ID: 344d6044d0ac
Revises: k7l8m9n0o1p2
Create Date: 2026-03-29 11:10:07.591925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '344d6044d0ac'
down_revision: Union[str, Sequence[str], None] = 'k7l8m9n0o1p2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('payments', sa.Column('currency', sa.String(length=10), server_default='USD', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('payments', 'currency')
