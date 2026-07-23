import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import AgentRun, QAHistory, ToolExecution
from app.security.redaction import safe_text_preview, stable_hash
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError

logger = logging.getLogger(__name__)
RUN_TYPES = {
    "rag_ask",
    "rag_retrieve",
    "session_review",
    "official_fetch",
    "official_audit",
    "official_apply",
}
RUN_STATUSES = {"running", "succeeded", "failed", "cancelled"}
OBSERVABILITY_STATUSES = {"disabled", "pending", "submitted", "failed"}


class RunHistoryService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create(
        self,
        db: Session,
        *,
        run_type: str,
        question: str | None = None,
        session_id: str | None = None,
        mode: str | None = None,
        source_types: list[str] | None = None,
        source_path_prefixes: list[str] | None = None,
    ) -> AgentRun:
        if run_type not in RUN_TYPES:
            raise ValueError("Unsupported run_type")
        preview_limit = min(200, self.settings.run_question_preview_chars)
        run = AgentRun(
            run_type=run_type,
            session_id=session_id,
            status="running",
            question_hash=stable_hash(question) if question else None,
            question_preview=safe_text_preview(question, preview_limit) if question else None,
            mode=mode,
            source_types=list(source_types or []),
            source_path_prefixes=[safe_text_preview(item, 300) for item in (source_path_prefixes or [])],
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(run)
        self._commit(db, "Failed to create agent run")
        db.refresh(run)
        return run

    def succeed(self, db: Session, run_id: str, *, state: dict[str, Any] | None = None) -> AgentRun:
        run = self.get(db, run_id)
        state = state or {}
        run.status = "succeeded"
        run.rewrite_applied = bool(state.get("rewrite_applied", False))
        if isinstance(state.get("source_types"), list):
            run.source_types = [str(item) for item in state["source_types"]]
        if isinstance(state.get("source_path_prefixes"), list):
            run.source_path_prefixes = [safe_text_preview(item, 300) for item in state["source_path_prefixes"]]
        if isinstance(state.get("retrieved_count"), int):
            run.retrieved_count = state["retrieved_count"]
        else:
            run.retrieved_count = len(state.get("reranked_results") or state.get("context_chunks") or [])
        if isinstance(state.get("source_count"), int):
            run.source_count = state["source_count"]
        else:
            run.source_count = len(state.get("context_sources") or [])
            if not run.source_count:
                run.source_count = int(
                    ((state.get("context_manifest") or {}).get("knowledge_context") or {}).get("sources") or 0
                )
        run.model_call_count = int(state.get("model_call_count") or 0)
        run.tool_call_count = int(state.get("tool_call_count") or 0)
        run.retry_count = int(state.get("retry_count") or 0)
        run.context_manifest = self._sanitize_manifest(state.get("context_manifest") or {})
        self._finish(run)
        self._commit(db, "Failed to complete agent run")
        db.refresh(run)
        return run

    def fail(self, db: Session, run_id: str, exc: Exception, *, error_code: str | None = None) -> AgentRun:
        run = self.get(db, run_id)
        run.status = "failed"
        run.error_code = (error_code or type(exc).__name__)[:100]
        run.error_message_preview = safe_text_preview(exc, min(500, self.settings.run_error_preview_chars))
        self._finish(run)
        self._commit(db, "Failed to record agent run failure")
        db.refresh(run)
        return run

    def safe_fail(self, db: Session, run_id: str | None, exc: Exception, *, error_code: str | None = None) -> None:
        if not run_id:
            return
        try:
            self.fail(db, run_id, exc, error_code=error_code)
        except Exception as history_exc:
            logger.warning("Run history failure recording failed: error_type=%s", type(history_exc).__name__)

    def safe_succeed(self, db: Session, run_id: str | None, *, state: dict[str, Any] | None = None) -> None:
        if not run_id:
            return
        try:
            self.succeed(db, run_id, state=state)
        except Exception as history_exc:
            logger.warning("Run history success recording failed: error_type=%s", type(history_exc).__name__)

    def set_observability(
        self,
        db: Session,
        run_id: str,
        *,
        provider: str | None,
        external_trace_id: str | None,
        status: str,
        error_code: str | None = None,
    ) -> AgentRun:
        if status not in OBSERVABILITY_STATUSES:
            raise ValueError("Unsupported observability status")
        run = self.get(db, run_id)
        run.observability_provider = provider[:30] if provider else None
        run.external_trace_id = external_trace_id[:36] if external_trace_id else None
        run.observability_status = status
        run.observability_error_code = error_code[:100] if error_code else None
        self._commit(db, "Failed to associate observability trace")
        db.refresh(run)
        return run

    def safe_set_observability(
        self,
        db: Session,
        run_id: str | None,
        *,
        provider: str | None,
        external_trace_id: str | None,
        status: str,
        error_code: str | None = None,
    ) -> None:
        if not run_id:
            return
        try:
            self.set_observability(
                db,
                run_id,
                provider=provider,
                external_trace_id=external_trace_id,
                status=status,
                error_code=error_code,
            )
        except Exception as history_exc:
            logger.warning(
                "Run observability association failed: error_type=%s",
                type(history_exc).__name__,
            )

    def get(self, db: Session, run_id: str) -> AgentRun:
        try:
            run = db.get(AgentRun, run_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get agent run") from exc
        if run is None:
            raise ResourceNotFoundError("Agent run not found")
        return run

    def list_records(
        self,
        db: Session,
        *,
        run_type: str | None = None,
        status: str | None = None,
        session_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[AgentRun]]:
        try:
            query = db.query(AgentRun)
            if run_type:
                query = query.filter(AgentRun.run_type == run_type)
            if status:
                query = query.filter(AgentRun.status == status)
            if session_id:
                query = query.filter(AgentRun.session_id == session_id)
            if created_after:
                query = query.filter(AgentRun.created_at >= created_after)
            if created_before:
                query = query.filter(AgentRun.created_at <= created_before)
            total = query.count()
            rows = query.order_by(AgentRun.created_at.desc()).offset(offset).limit(limit).all()
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list agent runs") from exc
        return total, rows

    def cleanup(self, db: Session, *, dry_run: bool, older_than_days: int) -> dict:
        if older_than_days < 1:
            raise ValueError("older_than_days must be at least 1")
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        try:
            run_ids = [row[0] for row in db.query(AgentRun.id).filter(AgentRun.created_at < cutoff).all()]
            tool_ids = [row[0] for row in db.query(ToolExecution.id).filter(ToolExecution.created_at < cutoff).all()]
            linked_qa = db.query(QAHistory).filter(QAHistory.run_id.in_(run_ids)).count() if run_ids else 0
            result = {
                "dry_run": dry_run,
                "older_than_days": older_than_days,
                "agent_runs": len(run_ids),
                "tool_executions": len(tool_ids),
                "qa_history_links_cleared": linked_qa,
            }
            if dry_run:
                return result
            if run_ids:
                db.query(QAHistory).filter(QAHistory.run_id.in_(run_ids)).update(
                    {QAHistory.run_id: None}, synchronize_session=False
                )
            if tool_ids:
                db.query(ToolExecution).filter(ToolExecution.id.in_(tool_ids)).delete(synchronize_session=False)
            if run_ids:
                db.query(AgentRun).filter(AgentRun.id.in_(run_ids)).delete(synchronize_session=False)
            db.commit()
            return result
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to clean run history") from exc

    @staticmethod
    def _sanitize_manifest(value: dict) -> dict:
        def compact(item: Any) -> Any:
            if isinstance(item, dict):
                return {str(key): compact(child) for key, child in item.items() if isinstance(child, (dict, bool, int, float, type(None)))}
            return item if isinstance(item, (bool, int, float, type(None))) else None

        result = compact(value)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _finish(run: AgentRun) -> None:
        finished = datetime.now(UTC)
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        run.finished_at = finished
        run.duration_ms = max(0, int((finished - started).total_seconds() * 1000))

    @staticmethod
    def _commit(db: Session, message: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError(message) from exc
