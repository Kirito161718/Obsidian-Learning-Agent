"""extend tool executions for read-only MCP

Revision ID: 20260720_0006
Revises: 20260719_0005
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa

revision = "20260720_0006"
down_revision = "20260719_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_executions",
        sa.Column("tool_source", sa.String(length=20), nullable=False, server_default="internal"),
    )
    op.add_column("tool_executions", sa.Column("provider_name", sa.String(length=100), nullable=True))
    op.add_column("tool_executions", sa.Column("remote_tool_name", sa.String(length=100), nullable=True))
    op.add_column("tool_executions", sa.Column("protocol_request_id", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_tool_executions_tool_source"), "tool_executions", ["tool_source"], unique=False)
    op.create_index(op.f("ix_tool_executions_provider_name"), "tool_executions", ["provider_name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_executions_provider_name"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_tool_source"), table_name="tool_executions")
    op.drop_column("tool_executions", "protocol_request_id")
    op.drop_column("tool_executions", "remote_tool_name")
    op.drop_column("tool_executions", "provider_name")
    op.drop_column("tool_executions", "tool_source")
