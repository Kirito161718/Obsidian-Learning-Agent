from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.services.rag_model_runtime import prewarm_rag_models
from app.services.system_status import collect_system_status

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
def system_status(db: Session = Depends(get_db)) -> dict:
    return collect_system_status(db)


@router.post("/models/prewarm")
def prewarm_models() -> dict:
    # An explicit operator request is sufficient authorization. This operation
    # loads only local models and runs content-free readiness probes.
    return prewarm_rag_models(get_settings(), force=True)
