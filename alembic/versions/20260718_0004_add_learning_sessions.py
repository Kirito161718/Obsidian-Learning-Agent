"""add local learning sessions and reviews

Revision ID: 20260718_0004
Revises: 20260717_0003
Create Date: 2026-07-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260718_0004"
down_revision = "20260717_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=True),
        sa.Column("learning_status_id", sa.String(length=36), nullable=True),
        sa.Column("source_types", sa.JSON(), nullable=False),
        sa.Column("source_path_prefixes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["learning_goals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["learning_status_id"], ["learning_status.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_learning_sessions_goal_id"), "learning_sessions", ["goal_id"], unique=False)
    op.create_index(
        op.f("ix_learning_sessions_learning_status_id"), "learning_sessions", ["learning_status_id"], unique=False
    )
    op.create_index(op.f("ix_learning_sessions_status"), "learning_sessions", ["status"], unique=False)
    op.create_index(
        op.f("ix_learning_sessions_last_activity_at"), "learning_sessions", ["last_activity_at"], unique=False
    )

    op.create_table(
        "learning_session_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_summary", sa.Text(), nullable=False),
        sa.Column("covered_topics", sa.JSON(), nullable=False),
        sa.Column("unresolved_questions", sa.JSON(), nullable=False),
        sa.Column("next_steps", sa.JSON(), nullable=False),
        sa.Column("source_qa_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["learning_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_learning_session_reviews_session_id"),
    )

    op.add_column("qa_history", sa.Column("session_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_qa_history_session_id_learning_sessions",
        "qa_history",
        "learning_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_qa_history_session_id"), "qa_history", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_qa_history_session_id"), table_name="qa_history")
    op.drop_constraint("fk_qa_history_session_id_learning_sessions", "qa_history", type_="foreignkey")
    op.drop_column("qa_history", "session_id")
    op.drop_table("learning_session_reviews")
    op.drop_index(op.f("ix_learning_sessions_last_activity_at"), table_name="learning_sessions")
    op.drop_index(op.f("ix_learning_sessions_status"), table_name="learning_sessions")
    op.drop_index(op.f("ix_learning_sessions_learning_status_id"), table_name="learning_sessions")
    op.drop_index(op.f("ix_learning_sessions_goal_id"), table_name="learning_sessions")
    op.drop_table("learning_sessions")
