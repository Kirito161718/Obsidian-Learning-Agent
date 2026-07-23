"""add LangSmith observability correlation to agent runs

Revision ID: 20260722_0008
Revises: 20260721_0007
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision = "20260722_0008"
down_revision = "20260721_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("observability_provider", sa.String(30), nullable=True))
    op.add_column("agent_runs", sa.Column("external_trace_id", sa.String(36), nullable=True))
    op.add_column("agent_runs", sa.Column("observability_status", sa.String(20), nullable=True))
    op.add_column("agent_runs", sa.Column("observability_error_code", sa.String(100), nullable=True))
    op.create_index(op.f("ix_agent_runs_external_trace_id"), "agent_runs", ["external_trace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_external_trace_id"), table_name="agent_runs")
    op.drop_column("agent_runs", "observability_error_code")
    op.drop_column("agent_runs", "observability_status")
    op.drop_column("agent_runs", "external_trace_id")
    op.drop_column("agent_runs", "observability_provider")
