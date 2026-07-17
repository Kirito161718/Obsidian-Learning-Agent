import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status as http_status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.goal import (
    GoalStatus,
    GoalType,
    LearningGoalCreate,
    LearningGoalListResponse,
    LearningGoalOut,
    LearningGoalUpdate,
)
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError
from app.services.goal_service import GoalService

router = APIRouter(prefix="/goals", tags=["learning-goals"])
logger = logging.getLogger(__name__)
service = GoalService()


def handle_service_error(exc: Exception) -> None:
    if isinstance(exc, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ServiceDatabaseError):
        logger.exception("Learning goal database operation failed")
        raise HTTPException(status_code=503, detail="Database is temporarily unavailable") from exc
    raise exc


@router.post("", response_model=LearningGoalOut, status_code=http_status.HTTP_201_CREATED)
def create_goal(payload: LearningGoalCreate, db: Session = Depends(get_db)) -> LearningGoalOut:
    try:
        return LearningGoalOut.model_validate(service.create(db, payload))
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.get("", response_model=LearningGoalListResponse)
def list_goals(
    goal_type: GoalType | None = None,
    status: GoalStatus | None = None,
    db: Session = Depends(get_db),
) -> LearningGoalListResponse:
    try:
        records = service.list_records(db, goal_type=goal_type, status=status)
    except ServiceDatabaseError as exc:
        handle_service_error(exc)
    items = [LearningGoalOut.model_validate(record) for record in records]
    return LearningGoalListResponse(total=len(items), items=items)


@router.get("/{goal_id}", response_model=LearningGoalOut)
def get_goal(goal_id: str, db: Session = Depends(get_db)) -> LearningGoalOut:
    try:
        return LearningGoalOut.model_validate(service.get(db, goal_id))
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.patch("/{goal_id}", response_model=LearningGoalOut)
def update_goal(goal_id: str, payload: LearningGoalUpdate, db: Session = Depends(get_db)) -> LearningGoalOut:
    try:
        return LearningGoalOut.model_validate(service.update(db, goal_id, payload))
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)


@router.delete("/{goal_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        service.delete(db, goal_id)
    except (ResourceNotFoundError, ServiceDatabaseError) as exc:
        handle_service_error(exc)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
