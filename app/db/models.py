from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime, MEDIUMBLOB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


PRECISE_DATETIME = DateTime(timezone=True).with_variant(MySQLDateTime(fsp=6), "mysql")
CHECKPOINT_BLOB = LargeBinary().with_variant(MEDIUMBLOB(), "mysql")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class QAHistory(Base):
    __tablename__ = "qa_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_chunks: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    qa_id: Mapped[str] = mapped_column(String(36), ForeignKey("qa_history.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningGoal(Base):
    __tablename__ = "learning_goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    related_topics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LearningStatus(Base):
    __tablename__ = "learning_status"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    topic: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started", index=True)
    mastery_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    manual_importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    current_problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_goal_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    related_note_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    project_relevance: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ObsidianNote(Base):
    __tablename__ = "obsidian_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # 700 utf8mb4 characters stay below MySQL's 3072-byte index-key limit.
    relative_path: Mapped[str] = mapped_column(String(700), nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    observed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frontmatter: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    outbound_links: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    index_status: Mapped[str] = mapped_column(String(30), nullable=False, default="discovered", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LearningSession(Base):
    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_goals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    learning_status_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_status.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_types: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=lambda: ["obsidian_note", "uploaded_document"]
    )
    source_path_prefixes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class LearningSessionReview(Base):
    __tablename__ = "learning_session_reviews"
    __table_args__ = (UniqueConstraint("session_id", name="uq_learning_session_reviews_session_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_summary: Mapped[str] = mapped_column(Text, nullable=False)
    covered_topics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    unresolved_questions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    next_steps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_qa_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    preferred_language: Mapped[str] = mapped_column(String(20), nullable=False, default="zh-CN")
    answer_depth: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    answer_style: Mapped[str] = mapped_column(String(20), nullable=False, default="structured")
    citation_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="required")
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("learning_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)
    question_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_preview: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_path_prefixes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rewrite_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retrieved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    official_source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    official_proposal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    official_snapshot_changed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    linked_note_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposal_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reindex_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    context_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tool_source: Mapped[str] = mapped_column(String(20), nullable=False, default="internal", index=True)
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    remote_tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    protocol_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    caller_type: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class OfficialSource(Base):
    __tablename__ = "official_sources"
    __table_args__ = (
        Index("ix_official_sources_enabled_status", "enabled", "status"),
        Index("ix_official_sources_allowed_host_created_at", "allowed_host", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    allowed_host: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")
    content_selector: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, onupdate=utc_now)


class OfficialSourceSnapshot(Base):
    __tablename__ = "official_source_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_official_snapshots_source_hash"),
        Index("ix_official_snapshots_source_created", "source_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("official_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_format: Mapped[str] = mapped_column(String(20), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(30), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(500), nullable=True)
    section_manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, index=True)


class OfficialSourceNoteLink(Base):
    __tablename__ = "official_source_note_links"
    __table_args__ = (
        UniqueConstraint("source_id", "obsidian_note_id", name="uq_official_source_note_link"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("official_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    obsidian_note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("obsidian_notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_heading_paths: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, onupdate=utc_now)


class UpdateProposal(Base):
    __tablename__ = "update_proposals"
    __table_args__ = (Index("ix_update_proposals_source_status_created", "source_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("official_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("official_source_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("official_source_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="building", index=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graph_thread_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    approval_request_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, index=True)
    ready_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)


class UpdateProposalItem(Base):
    __tablename__ = "update_proposal_items"
    __table_args__ = (
        UniqueConstraint("proposal_id", "obsidian_note_id", name="uq_update_proposal_note"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("update_proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    obsidian_note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("obsidian_notes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relative_path: Mapped[str] = mapped_column(String(700), nullable=False)
    base_note_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_note_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_content_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    diff_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    change_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    operation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    evidence_manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready", index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, onupdate=utc_now)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_target", "target_type", "target_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    operation_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="HIGH_RISK")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(2000), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, nullable=False, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(PRECISE_DATETIME, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, onupdate=utc_now)


class NoteVersion(Base):
    __tablename__ = "note_versions"
    __table_args__ = (
        UniqueConstraint("obsidian_note_id", "proposal_id", name="uq_note_versions_note_proposal"),
        Index("ix_note_versions_note_created", "obsidian_note_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    obsidian_note_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("obsidian_notes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("update_proposals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("official_source_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(700), nullable=False)
    before_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    after_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    diff_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, index=True)


class OfficialCheckpoint(Base):
    __tablename__ = "official_checkpoints"
    __table_args__ = (Index("ix_official_checkpoints_thread_created", "thread_id", "created_at"),)

    thread_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(100), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(50), nullable=False)
    checkpoint_blob: Mapped[bytes] = mapped_column(CHECKPOINT_BLOB, nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_blob: Mapped[bytes] = mapped_column(CHECKPOINT_BLOB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now, index=True)


class OfficialCheckpointWrite(Base):
    __tablename__ = "official_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(100), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    write_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value_blob: Mapped[bytes] = mapped_column(CHECKPOINT_BLOB, nullable=False)
    task_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(PRECISE_DATETIME, default=utc_now)
