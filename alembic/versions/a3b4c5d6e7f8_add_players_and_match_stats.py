"""add players and match_stats tables

Revision ID: a3b4c5d6e7f8
Revises: 721f865f6c27
Create Date: 2026-03-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = '721f865f6c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'players',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('nationality', sa.String(length=80), nullable=True),
        sa.Column('position', sa.String(length=30), nullable=False, server_default='UNKNOWN'),
        sa.Column('height_cm', sa.Integer(), nullable=True),
        sa.Column('weight_kg', sa.Integer(), nullable=True),
        sa.Column('foot', sa.String(length=20), nullable=False, server_default='UNKNOWN'),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('jersey_number', sa.Integer(), nullable=True),
        sa.Column('market_value_eur', sa.Integer(), nullable=True),
        sa.Column('contract_until', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_players_name', 'players', ['name'])
    op.create_index('ix_players_team', 'players', ['team_id'])

    op.create_table(
        'match_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('possession_pct', sa.Float(), nullable=True),
        sa.Column('shots', sa.Integer(), nullable=True),
        sa.Column('shots_on_target', sa.Integer(), nullable=True),
        sa.Column('xg', sa.Float(), nullable=True),
        sa.Column('xga', sa.Float(), nullable=True),
        sa.Column('corners', sa.Integer(), nullable=True),
        sa.Column('fouls', sa.Integer(), nullable=True),
        sa.Column('offsides', sa.Integer(), nullable=True),
        sa.Column('yellow_cards', sa.Integer(), nullable=True),
        sa.Column('red_cards', sa.Integer(), nullable=True),
        sa.Column('passes', sa.Integer(), nullable=True),
        sa.Column('pass_accuracy_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('match_id', 'team_id', name='uq_match_stats_match_team'),
    )
    op.create_index('ix_match_stats_match', 'match_stats', ['match_id'])


def downgrade() -> None:
    op.drop_index('ix_match_stats_match', table_name='match_stats')
    op.drop_table('match_stats')
    op.drop_index('ix_players_team', table_name='players')
    op.drop_index('ix_players_name', table_name='players')
    op.drop_table('players')
