"""add data_quality_report table

Revision ID: q7r8s9t0u1v2
Revises: p1q2r3s4t5u6
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_quality_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dqr_created_at", "data_quality_report", ["created_at"], unique=False)
    op.create_index(
        "ix_dqr_issue_type_created_at",
        "data_quality_report",
        ["issue_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dqr_issue_type_created_at", table_name="data_quality_report")
    op.drop_index("ix_dqr_created_at", table_name="data_quality_report")
    op.drop_table("data_quality_report")

