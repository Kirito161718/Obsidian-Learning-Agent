"""add Obsidian note metadata

Revision ID: 20260717_0003
Revises: 20260717_0002
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa

revision = "20260717_0003"
down_revision = "20260717_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "obsidian_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        # 700 utf8mb4 characters stay below MySQL's 3072-byte index-key limit.
        sa.Column("relative_path", sa.String(length=700), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("observed_hash", sa.String(length=64), nullable=True),
        sa.Column("indexed_hash", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frontmatter", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("outbound_links", sa.JSON(), nullable=False),
        sa.Column("index_status", sa.String(length=30), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index(op.f("ix_obsidian_notes_index_status"), "obsidian_notes", ["index_status"], unique=False)
    op.create_index(op.f("ix_obsidian_notes_indexed_hash"), "obsidian_notes", ["indexed_hash"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_obsidian_notes_indexed_hash"), table_name="obsidian_notes")
    op.drop_index(op.f("ix_obsidian_notes_index_status"), table_name="obsidian_notes")
    op.drop_table("obsidian_notes")
