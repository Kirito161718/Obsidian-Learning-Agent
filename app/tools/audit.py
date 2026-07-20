from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ToolExecution
from app.security.redaction import safe_text_preview, stable_hash, summarize_value
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


class ToolAuditService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create(
        self,
        db: Session,
        *,
        tool_name: str,
        risk_level: str | None,
        caller_type: str,
        decision: str,
        status: str,
        raw_input: dict,
        idempotency_key: str | None = None,
        run_id: str | None = None,
        error_code: str | None = None,
        tool_source: str = "internal",
        provider_name: str | None = None,
        remote_tool_name: str | None = None,
        protocol_request_id: str | None = None,
    ) -> ToolExecution:
        record = ToolExecution(
            run_id=run_id,
            tool_name=tool_name[:100],
            tool_source="mcp" if tool_source == "mcp" else "internal",
            provider_name=safe_text_preview(provider_name, 100) if provider_name else None,
            remote_tool_name=safe_text_preview(remote_tool_name, 100) if remote_tool_name else None,
            protocol_request_id=safe_text_preview(protocol_request_id, 100) if protocol_request_id else None,
            risk_level=risk_level,
            caller_type=caller_type,
            decision=decision,
            status=status,
            idempotency_key=idempotency_key,
            input_hash=stable_hash(raw_input),
            input_summary=summarize_value(raw_input, self.settings.tool_audit_input_max_chars),
            output_summary={},
            error_code=error_code,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        self._commit(db, "Failed to create tool execution audit")
        db.refresh(record)
        return record

    def mark_running(self, db: Session, record: ToolExecution) -> None:
        record.status = "running"
        record.started_at = datetime.now(UTC)
        self._commit(db, "Failed to start tool execution audit")

    def succeed(self, db: Session, record: ToolExecution, output: dict) -> ToolExecution:
        record.status = "succeeded"
        record.output_summary = summarize_value(output, self.settings.tool_audit_output_max_chars)
        self._finish(record)
        self._commit(db, "Failed to complete tool execution audit")
        db.refresh(record)
        return record

    def fail(self, db: Session, record: ToolExecution, *, error_code: str, error: object) -> ToolExecution:
        record.status = "failed"
        record.error_code = error_code[:100]
        record.error_message_preview = safe_text_preview(error, min(500, self.settings.run_error_preview_chars))
        self._finish(record)
        self._commit(db, "Failed to record tool execution failure")
        db.refresh(record)
        return record

    def get(self, db: Session, execution_id: str) -> ToolExecution:
        try:
            record = db.get(ToolExecution, execution_id)
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to get tool execution") from exc
        if record is None:
            raise ResourceNotFoundError("Tool execution not found")
        return record

    def list_records(
        self,
        db: Session,
        *,
        tool_name: str | None = None,
        risk_level: str | None = None,
        status: str | None = None,
        tool_source: str | None = None,
        provider_name: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[ToolExecution]]:
        try:
            query = db.query(ToolExecution)
            if tool_name:
                query = query.filter(ToolExecution.tool_name == tool_name)
            if risk_level:
                query = query.filter(ToolExecution.risk_level == risk_level)
            if status:
                query = query.filter(ToolExecution.status == status)
            if tool_source:
                query = query.filter(ToolExecution.tool_source == tool_source)
            if provider_name:
                query = query.filter(ToolExecution.provider_name == provider_name)
            total = query.count()
            rows = query.order_by(ToolExecution.created_at.desc()).offset(offset).limit(limit).all()
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list tool executions") from exc
        return total, rows

    @staticmethod
    def _finish(record: ToolExecution) -> None:
        finished = datetime.now(UTC)
        record.finished_at = finished
        started = record.started_at or record.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        record.duration_ms = max(0, int((finished - started).total_seconds() * 1000))

    @staticmethod
    def _commit(db: Session, message: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError(message) from exc
