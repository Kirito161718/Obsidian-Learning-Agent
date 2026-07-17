"""add learning goals and status

Revision ID: 20260717_0002
Revises: 20260613_0001
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa

revision = "20260717_0002"
down_revision = "20260613_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_goals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("goal_type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("related_topics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_learning_goals_goal_type"), "learning_goals", ["goal_type"], unique=False)
    op.create_index(op.f("ix_learning_goals_status"), "learning_goals", ["status"], unique=False)

    op.create_table(
        "learning_status",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("mastery_level", sa.Integer(), nullable=False),
        sa.Column("manual_importance", sa.Integer(), nullable=False),
        sa.Column("current_problem", sa.Text(), nullable=True),
        sa.Column("related_goal_ids", sa.JSON(), nullable=False),
        sa.Column("related_note_refs", sa.JSON(), nullable=False),
        sa.Column("project_relevance", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_learning_status_mastery_level"), "learning_status", ["mastery_level"], unique=False)
    op.create_index(op.f("ix_learning_status_status"), "learning_status", ["status"], unique=False)
    op.create_index(op.f("ix_learning_status_topic"), "learning_status", ["topic"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_learning_status_topic"), table_name="learning_status")
    op.drop_index(op.f("ix_learning_status_status"), table_name="learning_status")
    op.drop_index(op.f("ix_learning_status_mastery_level"), table_name="learning_status")
    op.drop_table("learning_status")
    op.drop_index(op.f("ix_learning_goals_status"), table_name="learning_goals")
    op.drop_index(op.f("ix_learning_goals_goal_type"), table_name="learning_goals")
    op.drop_table("learning_goals")
