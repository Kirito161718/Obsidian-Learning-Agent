"""add official source reminder preferences

Revision ID: 20260725_0010
Revises: 20260724_0009
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0010"
down_revision = "20260724_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "official_sources",
        sa.Column(
            "reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "official_sources",
        sa.Column(
            "check_interval_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )


def downgrade() -> None:
    op.drop_column("official_sources", "check_interval_days")
    op.drop_column("official_sources", "reminder_enabled")
