from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.schemas.agent import (
    AgentRunListResponse,
    AgentRunOut,
    CleanupHistoryRequest,
    CleanupHistoryResponse,
    ContextPreviewRequest,
    ContextPreviewResponse,
    ToolExecutionListResponse,
    ToolExecutionOut,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolMetadataOut,
    UserPreferenceOut,
    UserPreferenceUpdate,
)
from app.services.context_bundle_service import ContextBundleService
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.learning_context_service import LearningContextService
from app.services.run_history_service import RUN_STATUSES, RUN_TYPES, RunHistoryService
from app.services.session_memory_service import SessionMemoryService
from app.services.user_preference_service import UserPreferenceService
from app.tools.audit import ToolAuditService
from app.tools.definitions import CallerType, RiskLevel
from app.tools.executor import ToolExecutionError, ToolExecutor
from app.tools.registry import get_tool_registry

router = APIRouter(prefix="/agent", tags=["agent"])
settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
preferences = UserPreferenceService()
memory_service = SessionMemoryService()
learning_context_service = LearningContextService()
bundle_service = ContextBundleService()
run_history = RunHistoryService()
tool_audit = ToolAuditService()
logger = logging.getLogger(__name__)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def agent_console(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "agent.html")


@router.get("/preferences", response_model=UserPreferenceOut)
def get_preferences(db: Session = Depends(get_db)) -> UserPreferenceOut:
    try:
        return UserPreferenceOut.model_validate(preferences.get(db))
    except ServiceDatabaseError as exc:
        _database_error(exc)


@router.patch("/preferences", response_model=UserPreferenceOut)
def update_preferences(
    payload: UserPreferenceUpdate,
    db: Session = Depends(get_db),
) -> UserPreferenceOut:
    try:
        return UserPreferenceOut.model_validate(preferences.update(db, payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ServiceDatabaseError as exc:
        _database_error(exc)


@router.post("/context/preview", response_model=ContextPreviewResponse)
def preview_context(
    payload: ContextPreviewRequest,
    db: Session = Depends(get_db),
) -> ContextPreviewResponse:
    try:
        preference_data = preferences.get(db)
        prompt_preferences = _prompt_preferences(preference_data)
        resolved = learning_context_service.resolve(
            db,
            session_id=payload.session_id,
            goal_id=payload.goal_id,
            learning_status_id=payload.learning_status_id,
        )
        memory, memory_truncated = memory_service.load_with_metadata(db, payload.session_id)
        bundle = bundle_service.build(
            user_preferences=prompt_preferences,
            explicit_learning_context=resolved["context"],
            session_memory=memory,
            knowledge_context="",
            knowledge_source_count=0,
            session_memory_truncated=memory_truncated,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceDatabaseError as exc:
        _database_error(exc)
    manifest = bundle.manifest
    return ContextPreviewResponse(
        user_preferences={
            "preferred_language": preference_data["preferred_language"],
            "answer_depth": preference_data["answer_depth"],
            "answer_style": preference_data["answer_style"],
            "citation_mode": preference_data["citation_mode"],
            "custom_instructions_chars": len(preference_data.get("custom_instructions") or ""),
        },
        explicit_learning_context={
            "goal_included": "goal" in bundle.explicit_learning_context,
            "learning_status_included": "learning_status" in bundle.explicit_learning_context,
        },
        session_memory_turns=len(bundle.session_memory),
        section_chars={
            "user_preferences": manifest["user_preferences"]["chars"],
            "explicit_learning_context": manifest["explicit_learning_context"]["chars"],
            "session_memory": manifest["session_memory"]["chars"],
            "knowledge_context": manifest["knowledge_context"]["chars"],
        },
        truncated={
            name: bool(manifest[name]["truncated"])
            for name in ("user_preferences", "explicit_learning_context", "session_memory", "knowledge_context")
        },
        context_manifest=manifest,
    )


@router.get("/runs", response_model=AgentRunListResponse)
def list_runs(
    run_type: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AgentRunListResponse:
    if run_type and run_type not in RUN_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported run_type")
    if status and status not in RUN_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported run status")
    try:
        total, rows = run_history.list_records(
            db,
            run_type=run_type,
            status=status,
            session_id=session_id,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )
    except ServiceDatabaseError as exc:
        _database_error(exc)
    return AgentRunListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[AgentRunOut.model_validate(row) for row in rows],
    )


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> AgentRunOut:
    try:
        return AgentRunOut.model_validate(run_history.get(db, run_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceDatabaseError as exc:
        _database_error(exc)


@router.get("/tools", response_model=list[ToolMetadataOut])
def list_tools() -> list[ToolMetadataOut]:
    return [ToolMetadataOut.model_validate(item.metadata()) for item in get_tool_registry().list()]


@router.get("/tools/{tool_name}", response_model=ToolMetadataOut)
def get_tool(tool_name: str) -> ToolMetadataOut:
    definition = get_tool_registry().get(tool_name)
    if definition is None:
        raise HTTPException(status_code=404, detail="Tool is not registered")
    return ToolMetadataOut.model_validate(definition.metadata())


@router.post("/tools/{tool_name}/execute", response_model=ToolExecutionResult)
async def execute_tool(
    tool_name: str,
    payload: ToolExecutionRequest,
    db: Session = Depends(get_db),
) -> ToolExecutionResult:
    try:
        output, execution = await ToolExecutor().execute(
            db,
            tool_name=tool_name,
            raw_input=payload.input,
            caller=CallerType.user,
            idempotency_key=payload.idempotency_key,
        )
    except ToolExecutionError as exc:
        if exc.code == "tool_not_registered":
            status_code = 404
        elif exc.code in {"input_validation_failed", "output_validation_failed", "mcp_input_invalid", "mcp_schema_invalid"}:
            status_code = 422
        elif exc.code in {"mcp_server_not_connected", "mcp_tool_not_allowed"} or exc.decision == "approval_required":
            status_code = 409
        elif exc.code in {"mcp_timeout", "mcp_protocol_error", "mcp_server_error", "mcp_connection_failed"}:
            status_code = 503
        else:
            status_code = 403
        raise HTTPException(
            status_code=status_code,
            detail={
                "error_code": exc.code,
                "decision": exc.decision,
                "execution_id": exc.execution_id,
                "message": str(exc),
            },
        ) from exc
    return ToolExecutionResult(
        execution=ToolExecutionOut.model_validate(execution),
        output=output,
    )


@router.get("/tool-executions", response_model=ToolExecutionListResponse)
def list_tool_executions(
    tool_name: str | None = None,
    risk_level: RiskLevel | None = None,
    status: str | None = None,
    tool_source: str | None = None,
    provider_name: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ToolExecutionListResponse:
    try:
        total, rows = tool_audit.list_records(
            db,
            tool_name=tool_name,
            risk_level=risk_level.value if risk_level else None,
            status=status,
            tool_source=tool_source,
            provider_name=provider_name,
            limit=limit,
            offset=offset,
        )
    except ServiceDatabaseError as exc:
        _database_error(exc)
    return ToolExecutionListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[ToolExecutionOut.model_validate(row) for row in rows],
    )


@router.get("/tool-executions/{execution_id}", response_model=ToolExecutionOut)
def get_tool_execution(execution_id: str, db: Session = Depends(get_db)) -> ToolExecutionOut:
    try:
        return ToolExecutionOut.model_validate(tool_audit.get(db, execution_id))
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceDatabaseError as exc:
        _database_error(exc)


@router.post("/maintenance/cleanup-history", response_model=CleanupHistoryResponse)
def cleanup_history(
    payload: CleanupHistoryRequest,
    db: Session = Depends(get_db),
) -> CleanupHistoryResponse:
    try:
        return CleanupHistoryResponse.model_validate(
            run_history.cleanup(db, dry_run=payload.dry_run, older_than_days=payload.older_than_days)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ServiceDatabaseError as exc:
        _database_error(exc)


def _prompt_preferences(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in (
            "preferred_language",
            "answer_depth",
            "answer_style",
            "citation_mode",
            "custom_instructions",
        )
    }


def _database_error(exc: Exception) -> None:
    logger.warning("Agent database operation failed: error_type=%s", type(exc).__name__)
    raise HTTPException(status_code=503, detail="Database is temporarily unavailable") from exc
