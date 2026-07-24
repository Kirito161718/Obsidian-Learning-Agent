"""add reproducible RAG evaluation records

Revision ID: 20260724_0009
Revises: 20260722_0008
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0009"
down_revision = "20260722_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_eval_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_rag_eval_dataset_version"),
    )
    op.create_index("ix_rag_eval_datasets_content_hash", "rag_eval_datasets", ["content_hash"])
    op.create_index("ix_rag_eval_datasets_status", "rag_eval_datasets", ["status"])
    op.create_index("ix_rag_eval_dataset_status_created", "rag_eval_datasets", ["status", "created_at"])

    op.create_table(
        "rag_retrieval_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_key", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("collection_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evaluation_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("promotion_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("manual_exception", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rollback_profile_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["rollback_profile_id"], ["rag_retrieval_profiles.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("profile_key", "version", name="uq_rag_retrieval_profile_version"),
    )
    op.create_index("ix_rag_retrieval_profiles_configuration_hash", "rag_retrieval_profiles", ["configuration_hash"])
    op.create_index("ix_rag_retrieval_profiles_is_active", "rag_retrieval_profiles", ["is_active"])
    op.create_index("ix_rag_retrieval_profiles_is_baseline", "rag_retrieval_profiles", ["is_baseline"])
    op.create_index("ix_rag_profile_active_baseline", "rag_retrieval_profiles", ["is_active", "is_baseline"])

    op.create_table(
        "rag_eval_experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("profile_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("variant_key", sa.String(120), nullable=False),
        sa.Column("experiment_type", sa.String(30), nullable=False, server_default="retrieval"),
        sa.Column("retrieval_profile_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("metric_summary", sa.JSON(), nullable=False),
        sa.Column("failure_summary", sa.JSON(), nullable=False),
        sa.Column("langsmith_experiment_name", sa.String(250), nullable=True),
        sa.Column("langsmith_experiment_id", sa.String(80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_preview", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["rag_eval_datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_id"], ["rag_retrieval_profiles.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("name", name="uq_rag_eval_experiment_name"),
    )
    op.create_index("ix_rag_eval_experiments_dataset_id", "rag_eval_experiments", ["dataset_id"])
    op.create_index("ix_rag_eval_experiments_profile_id", "rag_eval_experiments", ["profile_id"])
    op.create_index("ix_rag_eval_experiments_status", "rag_eval_experiments", ["status"])
    op.create_index("ix_rag_eval_experiments_variant_key", "rag_eval_experiments", ["variant_key"])
    op.create_index("ix_rag_eval_experiment_status_created", "rag_eval_experiments", ["status", "created_at"])

    op.create_table(
        "rag_eval_case_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(120), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("repeat_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metric_values", sa.JSON(), nullable=False),
        sa.Column("retrieval_stats", sa.JSON(), nullable=False),
        sa.Column("answer_stats", sa.JSON(), nullable=False),
        sa.Column("internal_run_id", sa.String(36), nullable=True),
        sa.Column("external_trace_id", sa.String(80), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_token_count", sa.Integer(), nullable=True),
        sa.Column("output_token_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["rag_eval_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("experiment_id", "case_id", "repeat_index", name="uq_rag_eval_case_repeat"),
    )
    op.create_index("ix_rag_eval_case_results_experiment_id", "rag_eval_case_results", ["experiment_id"])
    op.create_index("ix_rag_eval_case_results_category", "rag_eval_case_results", ["category"])
    op.create_index("ix_rag_eval_case_experiment_status", "rag_eval_case_results", ["experiment_id", "status"])


def downgrade() -> None:
    op.drop_table("rag_eval_case_results")
    op.drop_table("rag_eval_experiments")
    op.drop_table("rag_retrieval_profiles")
    op.drop_table("rag_eval_datasets")
