import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status as http_status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import LearningStatus
from app.schemas.learning_status import (
    LearningStatusCreate,
    LearningStatusListResponse,
    LearningStatusOut,
    LearningStatusUpdate,
    LearningStatusValue,
)
from app.services.crud_errors import InvalidReferenceError, ResourceNotFoundError, ServiceDatabaseError
from app.services.importance_service import ImportanceService
from app.services.learning_status_service import LearningStatusService

router = APIRouter(prefix="/learning-status", tags=["learning-status"])
logger = logging.getLogger(__name__)
service = LearningStatusService()
importance_service = ImportanceService()


def handle_service_error(exc: Exception) -> None:
    if isinstance(exc, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, InvalidReferenceError):
        raise HTTPException(
            status_code=422,
            detail={"message": "Unknown learning goal IDs", "missing_goal_ids": exc.missing_ids},
        ) from exc
    if isinstance(exc, ServiceDatabaseError):
        logger.exception("Learning status database operation failed")
        raise HTTPException(status_code=503, detail="Database is temporarily unavailable") from exc
    raise exc


def to_output(record: LearningStatus, goals_by_id: dict) -> LearningStatusOut:
    importance = importance_service.calculate(record, goals_by_id)
    return LearningStatusOut.model_validate(
        {
            "id": record.id,
            "topic": record.topic,
            "status": record.status,
            "mastery_level": record.mastery_level,
            "manual_importance": record.manual_importance,
            "current_problem": record.current_problem,
            "related_goal_ids": record.related_goal_ids,
            "related_note_refs": record.related_note_refs,
            "project_relevance": record.project_relevance,
            "importance_score": importance.importance_score,
            "importance_stars": importance.importance_stars,
            "importance_breakdown": importance.importance_breakdown,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
    )


def output_records(db: Session, records: list[LearningStatus]) -> list[LearningStatusOut]:
    goal_ids = {goal_id for record in records for goal_id in record.related_goal_ids}
    goals_by_id = service.goals_by_id(db, goal_ids)
    return [to_output(record, goals_by_id) for record in records]


@router.post("", response_model=LearningStatusOut, status_code=http_status.HTTP_201_CREATED)
def create_learning_status(
    payload: LearningStatusCreate,
    db: Session = Depends(get_db),
) -> LearningStatusOut:
    try:
        record = service.create(db, payload)
        return output_records(db, [record])[0]
    except (InvalidReferenceError, ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.get("", response_model=LearningStatusListResponse)
def list_learning_statuses(
    status: LearningStatusValue | None = None,
    min_mastery: int | None = Query(default=None, ge=0, le=5),
    max_mastery: int | None = Query(default=None, ge=0, le=5),
    related_goal_id: str | None = None,
    db: Session = Depends(get_db),
) -> LearningStatusListResponse:
    if min_mastery is not None and max_mastery is not None and min_mastery > max_mastery:
        raise HTTPException(status_code=422, detail="min_mastery must be less than or equal to max_mastery")
    try:
        records = service.list_records(
            db,
            status=status,
            min_mastery=min_mastery,
            max_mastery=max_mastery,
            related_goal_id=related_goal_id,
        )
        items = output_records(db, records)
    except ServiceDatabaseError as exc:
        handle_service_error(exc)
    return LearningStatusListResponse(total=len(items), items=items)


@router.get("/{status_id}", response_model=LearningStatusOut)
def get_learning_status(status_id: str, db: Session = Depends(get_db)) -> LearningStatusOut:
    try:
        return output_records(db, [service.get(db, status_id)])[0]
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.patch("/{status_id}", response_model=LearningStatusOut)
def update_learning_status(
    status_id: str,
    payload: LearningStatusUpdate,
    db: Session = Depends(get_db),
) -> LearningStatusOut:
    try:
        record = service.update(db, status_id, payload)
        return output_records(db, [record])[0]
    except (InvalidReferenceError, ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.delete("/{status_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_learning_status(status_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        service.delete(db, status_id)
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
