"""add memory context tools and run audit foundation

Revision ID: 20260719_0005
Revises: 20260718_0004
Create Date: 2026-07-19
"""

from alembic import op
import sqlalchemy as sa

revision = "20260719_0005"
down_revision = "20260718_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("preferred_language", sa.String(length=20), nullable=False),
        sa.Column("answer_depth", sa.String(length=20), nullable=False),
        sa.Column("answer_style", sa.String(length=20), nullable=False),
        sa.Column("citation_mode", sa.String(length=30), nullable=False),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "learning_sessions",
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_type", sa.String(length=30), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=True),
        sa.Column("question_preview", sa.String(length=200), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("source_types", sa.JSON(), nullable=False),
        sa.Column("source_path_prefixes", sa.JSON(), nullable=False),
        sa.Column("rewrite_applied", sa.Boolean(), nullable=False),
        sa.Column("retrieved_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("context_manifest", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_preview", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["learning_sessions.id"], name="fk_agent_runs_session_id_learning_sessions", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_runs_run_type"), "agent_runs", ["run_type"], unique=False)
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False)
    op.create_index(op.f("ix_agent_runs_session_id"), "agent_runs", ["session_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_created_at"), "agent_runs", ["created_at"], unique=False)

    op.add_column("qa_history", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_qa_history_run_id_agent_runs",
        "qa_history",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_qa_history_run_id"), "qa_history", ["run_id"], unique=False)

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("caller_type", sa.String(length=20), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_preview", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name="fk_tool_executions_run_id_agent_runs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_executions_run_id"), "tool_executions", ["run_id"], unique=False)
    op.create_index(op.f("ix_tool_executions_tool_name"), "tool_executions", ["tool_name"], unique=False)
    op.create_index(op.f("ix_tool_executions_risk_level"), "tool_executions", ["risk_level"], unique=False)
    op.create_index(op.f("ix_tool_executions_status"), "tool_executions", ["status"], unique=False)
    op.create_index(op.f("ix_tool_executions_created_at"), "tool_executions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_constraint("fk_tool_executions_run_id_agent_runs", "tool_executions", type_="foreignkey")
    op.drop_constraint("fk_qa_history_run_id_agent_runs", "qa_history", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_session_id_learning_sessions", "agent_runs", type_="foreignkey")

    op.drop_index(op.f("ix_tool_executions_created_at"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_status"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_risk_level"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_tool_name"), table_name="tool_executions")
    op.drop_index(op.f("ix_tool_executions_run_id"), table_name="tool_executions")
    op.drop_index(op.f("ix_qa_history_run_id"), table_name="qa_history")
    op.drop_index(op.f("ix_agent_runs_created_at"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_session_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_run_type"), table_name="agent_runs")

    op.drop_column("qa_history", "run_id")
    op.drop_column("learning_sessions", "memory_enabled")

    op.drop_table("tool_executions")
    op.drop_table("agent_runs")
    op.drop_table("user_preferences")
