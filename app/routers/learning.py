import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status as http_status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.routers.qa import parse_source_chunks, sanitize_source_chunk
from app.schemas.learning import (
    LearningSessionCreate,
    LearningSessionListResponse,
    LearningSessionMessage,
    LearningSessionMessagesResponse,
    LearningSessionOut,
    LearningSessionReviewOut,
    LearningSessionStatus,
    LearningSessionUpdate,
)
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.learning_review_service import (
    EmptyLearningSessionError,
    InvalidReviewOutputError,
    LearningReviewService,
)
from app.services.learning_session_service import InvalidSessionReferenceError, LearningSessionService
from app.services.llm_service import LLMServiceUnavailable

router = APIRouter(tags=["learning"])
logger = logging.getLogger(__name__)
settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
session_service = LearningSessionService()
review_service = LearningReviewService()


@router.get("/learning", response_class=HTMLResponse, include_in_schema=False)
def learning_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "learning.html",
        {"obsidian_vault_name": settings.obsidian_vault_path.name if settings.obsidian_vault_path else ""},
    )


@router.post(
    "/learning/sessions",
    response_model=LearningSessionOut,
    status_code=http_status.HTTP_201_CREATED,
)
def create_session(payload: LearningSessionCreate, db: Session = Depends(get_db)) -> LearningSessionOut:
    try:
        return LearningSessionOut.model_validate(session_service.create(db, payload))
    except Exception as exc:
        _handle_error(exc)


@router.get("/learning/sessions", response_model=LearningSessionListResponse)
def list_sessions(
    status: LearningSessionStatus | None = LearningSessionStatus.active,
    goal_id: str | None = None,
    learning_status_id: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> LearningSessionListResponse:
    try:
        total, records = session_service.list_records(
            db,
            status=status.value if status else None,
            goal_id=goal_id,
            learning_status_id=learning_status_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _handle_error(exc)
    return LearningSessionListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[LearningSessionOut.model_validate(record) for record in records],
    )


@router.get("/learning/sessions/{session_id}", response_model=LearningSessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> LearningSessionOut:
    try:
        return LearningSessionOut.model_validate(session_service.get(db, session_id))
    except Exception as exc:
        _handle_error(exc)


@router.patch("/learning/sessions/{session_id}", response_model=LearningSessionOut)
def update_session(
    session_id: str,
    payload: LearningSessionUpdate,
    db: Session = Depends(get_db),
) -> LearningSessionOut:
    try:
        return LearningSessionOut.model_validate(session_service.update(db, session_id, payload))
    except Exception as exc:
        _handle_error(exc)


@router.post("/learning/sessions/{session_id}/archive", response_model=LearningSessionOut)
def archive_session(session_id: str, db: Session = Depends(get_db)) -> LearningSessionOut:
    try:
        return LearningSessionOut.model_validate(session_service.archive(db, session_id))
    except Exception as exc:
        _handle_error(exc)


@router.post("/learning/sessions/{session_id}/restore", response_model=LearningSessionOut)
def restore_session(session_id: str, db: Session = Depends(get_db)) -> LearningSessionOut:
    try:
        return LearningSessionOut.model_validate(session_service.restore(db, session_id))
    except Exception as exc:
        _handle_error(exc)


@router.get("/learning/sessions/{session_id}/messages", response_model=LearningSessionMessagesResponse)
def list_session_messages(
    session_id: str,
    db: Session = Depends(get_db),
) -> LearningSessionMessagesResponse:
    try:
        rows = session_service.messages(db, session_id)
    except Exception as exc:
        _handle_error(exc)
    items = [
        LearningSessionMessage(
            id=row.id,
            question=row.question,
            rewritten_query=row.rewritten_query,
            answer=row.answer,
            source_chunks=[sanitize_source_chunk(item) for item in parse_source_chunks(row.source_chunks, row.id)],
            created_at=row.created_at,
        )
        for row in rows
    ]
    return LearningSessionMessagesResponse(session_id=session_id, total=len(items), items=items)


@router.post("/learning/sessions/{session_id}/review", response_model=LearningSessionReviewOut)
def generate_review(session_id: str, db: Session = Depends(get_db)) -> LearningSessionReviewOut:
    try:
        return LearningSessionReviewOut.model_validate(review_service.generate(db, session_id))
    except Exception as exc:
        _handle_error(exc)


@router.get("/learning/sessions/{session_id}/review", response_model=LearningSessionReviewOut)
def get_review(session_id: str, db: Session = Depends(get_db)) -> LearningSessionReviewOut:
    try:
        return LearningSessionReviewOut.model_validate(review_service.get(db, session_id))
    except Exception as exc:
        _handle_error(exc)


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, EmptyLearningSessionError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, InvalidReviewOutputError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, (InvalidSessionReferenceError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, LLMServiceUnavailable):
        raise HTTPException(status_code=503, detail="LLM service is temporarily unavailable") from exc
    if isinstance(exc, ServiceDatabaseError):
        logger.exception("Learning workspace database operation failed")
        raise HTTPException(status_code=503, detail="Database is temporarily unavailable") from exc
    raise exc
