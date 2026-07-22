"""add official source audit, approval, versioning, and checkpoints

Revision ID: 20260721_0007
Revises: 20260720_0006
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260721_0007"
down_revision = "20260720_0006"
branch_labels = None
depends_on = None


def precise_datetime():
    return sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


def checkpoint_blob():
    return sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("official_source_id", sa.String(36), nullable=True))
    op.add_column("agent_runs", sa.Column("official_proposal_id", sa.String(36), nullable=True))
    op.add_column("agent_runs", sa.Column("official_snapshot_changed", sa.Boolean(), nullable=True))
    op.add_column("agent_runs", sa.Column("linked_note_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_runs", sa.Column("proposal_item_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_runs", sa.Column("approval_status", sa.String(20), nullable=True))
    op.add_column("agent_runs", sa.Column("applied_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_runs", sa.Column("reindex_status", sa.String(30), nullable=True))
    op.create_index(op.f("ix_agent_runs_official_source_id"), "agent_runs", ["official_source_id"])
    op.create_index(op.f("ix_agent_runs_official_proposal_id"), "agent_runs", ["official_proposal_id"])

    op.create_table(
        "official_sources",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("allowed_host", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("content_selector", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_checked_at", precise_datetime(), nullable=True),
        sa.Column("last_success_at", precise_datetime(), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_preview", sa.String(500), nullable=True),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.Column("updated_at", precise_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_official_sources_allowed_host"), "official_sources", ["allowed_host"])
    op.create_index(op.f("ix_official_sources_enabled"), "official_sources", ["enabled"])
    op.create_index(op.f("ix_official_sources_status"), "official_sources", ["status"])
    op.create_index(op.f("ix_official_sources_created_at"), "official_sources", ["created_at"])
    op.create_index("ix_official_sources_enabled_status", "official_sources", ["enabled", "status"])
    op.create_index(
        "ix_official_sources_allowed_host_created_at", "official_sources", ["allowed_host", "created_at"]
    )

    op.create_table(
        "official_source_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("normalized_format", sa.String(20), nullable=False),
        sa.Column("normalizer_version", sa.String(30), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(500), nullable=True),
        sa.Column("last_modified", sa.String(500), nullable=True),
        sa.Column("section_manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["official_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_official_snapshots_source_hash"),
    )
    op.create_index(op.f("ix_official_source_snapshots_source_id"), "official_source_snapshots", ["source_id"])
    op.create_index(op.f("ix_official_source_snapshots_content_hash"), "official_source_snapshots", ["content_hash"])
    op.create_index(op.f("ix_official_source_snapshots_created_at"), "official_source_snapshots", ["created_at"])
    op.create_index(
        "ix_official_snapshots_source_created", "official_source_snapshots", ["source_id", "created_at"]
    )

    op.create_table(
        "official_source_note_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("obsidian_note_id", sa.String(36), nullable=False),
        sa.Column("target_heading_paths", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.Column("updated_at", precise_datetime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["official_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["obsidian_note_id"], ["obsidian_notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "obsidian_note_id", name="uq_official_source_note_link"),
    )
    op.create_index(op.f("ix_official_source_note_links_source_id"), "official_source_note_links", ["source_id"])
    op.create_index(
        op.f("ix_official_source_note_links_obsidian_note_id"), "official_source_note_links", ["obsidian_note_id"]
    )

    op.create_table(
        "update_proposals",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("summary", sa.String(2000), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("graph_thread_id", sa.String(100), nullable=False),
        sa.Column("approval_request_id", sa.String(36), nullable=True),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.Column("ready_at", precise_datetime(), nullable=True),
        sa.Column("approved_at", precise_datetime(), nullable=True),
        sa.Column("rejected_at", precise_datetime(), nullable=True),
        sa.Column("applied_at", precise_datetime(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_preview", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["official_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["official_source_snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_snapshot_id"], ["official_source_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_thread_id"),
        sa.UniqueConstraint("approval_request_id"),
    )
    for column in ("source_id", "snapshot_id", "status", "payload_hash", "created_at"):
        op.create_index(op.f(f"ix_update_proposals_{column}"), "update_proposals", [column])
    op.create_index(
        "ix_update_proposals_source_status_created", "update_proposals", ["source_id", "status", "created_at"]
    )

    op.create_table(
        "update_proposal_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("obsidian_note_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(700), nullable=False),
        sa.Column("base_note_hash", sa.String(64), nullable=False),
        sa.Column("proposed_note_hash", sa.String(64), nullable=False),
        sa.Column("proposed_content_path", sa.String(1024), nullable=False),
        sa.Column("diff_path", sa.String(1024), nullable=False),
        sa.Column("change_ratio", sa.Float(), nullable=False),
        sa.Column("operation_count", sa.Integer(), nullable=False),
        sa.Column("operation_manifest", sa.JSON(), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_preview", sa.String(500), nullable=True),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.Column("updated_at", precise_datetime(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["update_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["obsidian_note_id"], ["obsidian_notes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "obsidian_note_id", name="uq_update_proposal_note"),
    )
    for column in ("proposal_id", "obsidian_note_id", "status"):
        op.create_index(op.f(f"ix_update_proposal_items_{column}"), "update_proposal_items", [column])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_type", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(2000), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("expires_at", precise_datetime(), nullable=False),
        sa.Column("decided_at", precise_datetime(), nullable=True),
        sa.Column("consumed_at", precise_datetime(), nullable=True),
        sa.Column("decision_note", sa.String(1000), nullable=True),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.Column("updated_at", precise_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("operation_type", "status", "expires_at"):
        op.create_index(op.f(f"ix_approval_requests_{column}"), "approval_requests", [column])
    op.create_index("ix_approval_requests_target", "approval_requests", ["target_type", "target_id"])
    op.create_foreign_key(
        "fk_update_proposals_approval_request", "update_proposals", "approval_requests",
        ["approval_request_id"], ["id"], ondelete="SET NULL"
    )

    op.create_table(
        "note_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("obsidian_note_id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(700), nullable=False),
        sa.Column("before_hash", sa.String(64), nullable=False),
        sa.Column("after_hash", sa.String(64), nullable=False),
        sa.Column("backup_path", sa.String(1024), nullable=False),
        sa.Column("diff_path", sa.String(1024), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.ForeignKeyConstraint(["obsidian_note_id"], ["obsidian_notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposal_id"], ["update_proposals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["official_source_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("obsidian_note_id", "proposal_id", name="uq_note_versions_note_proposal"),
    )
    for column in ("obsidian_note_id", "proposal_id", "pinned", "created_at"):
        op.create_index(op.f(f"ix_note_versions_{column}"), "note_versions", [column])
    op.create_index("ix_note_versions_note_created", "note_versions", ["obsidian_note_id", "created_at"])

    op.create_table(
        "official_checkpoints",
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("checkpoint_ns", sa.String(100), nullable=False),
        sa.Column("checkpoint_id", sa.String(100), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(100), nullable=True),
        sa.Column("checkpoint_type", sa.String(50), nullable=False),
        sa.Column("checkpoint_blob", checkpoint_blob(), nullable=False),
        sa.Column("metadata_type", sa.String(50), nullable=False),
        sa.Column("metadata_blob", checkpoint_blob(), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_index(op.f("ix_official_checkpoints_created_at"), "official_checkpoints", ["created_at"])
    op.create_index(
        "ix_official_checkpoints_thread_created", "official_checkpoints", ["thread_id", "created_at"]
    )

    op.create_table(
        "official_checkpoint_writes",
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("checkpoint_ns", sa.String(100), nullable=False),
        sa.Column("checkpoint_id", sa.String(100), nullable=False),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("write_index", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(50), nullable=False),
        sa.Column("value_blob", checkpoint_blob(), nullable=False),
        sa.Column("task_path", sa.String(500), nullable=False),
        sa.Column("created_at", precise_datetime(), nullable=False),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index"),
    )


def downgrade() -> None:
    op.drop_table("official_checkpoint_writes")
    op.drop_index("ix_official_checkpoints_thread_created", table_name="official_checkpoints")
    op.drop_index(op.f("ix_official_checkpoints_created_at"), table_name="official_checkpoints")
    op.drop_table("official_checkpoints")
    op.drop_table("note_versions")
    op.drop_constraint("fk_update_proposals_approval_request", "update_proposals", type_="foreignkey")
    op.drop_table("approval_requests")
    op.drop_table("update_proposal_items")
    op.drop_table("update_proposals")
    op.drop_table("official_source_note_links")
    op.drop_table("official_source_snapshots")
    op.drop_table("official_sources")
    op.drop_index(op.f("ix_agent_runs_official_proposal_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_official_source_id"), table_name="agent_runs")
    for column in (
        "reindex_status", "applied_count", "approval_status", "proposal_item_count",
        "linked_note_count", "official_snapshot_changed", "official_proposal_id", "official_source_id"
    ):
        op.drop_column("agent_runs", column)
