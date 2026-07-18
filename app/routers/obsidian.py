from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.obsidian.scanner import VaultConfigurationError
from app.schemas.obsidian import (
    ObsidianChunkListResponse,
    ObsidianIndexRequest,
    ObsidianNoteListResponse,
    ObsidianNoteOut,
    ObsidianOperationResponse,
    ObsidianStatusResponse,
)
from app.services.obsidian_index_service import ObsidianDisabledError, ObsidianIndexService
from app.services.obsidian_note_service import ObsidianNoteNotFoundError, ObsidianNoteService
from app.services.vector_service import VectorService

router = APIRouter(prefix="/obsidian", tags=["obsidian"])


@lru_cache
def get_obsidian_index_service() -> ObsidianIndexService:
    return ObsidianIndexService()


@lru_cache
def get_obsidian_note_service() -> ObsidianNoteService:
    return ObsidianNoteService()


@router.get("/status", response_model=ObsidianStatusResponse)
def status(
    db: Session = Depends(get_db),
    service: ObsidianIndexService = Depends(get_obsidian_index_service),
) -> dict:
    return service.status(db)


@router.post("/scan", response_model=ObsidianOperationResponse)
def scan(
    db: Session = Depends(get_db),
    service: ObsidianIndexService = Depends(get_obsidian_index_service),
) -> dict:
    try:
        return service.scan(db)
    except (ObsidianDisabledError, VaultConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/index", response_model=ObsidianOperationResponse)
def index(
    request: ObsidianIndexRequest | None = None,
    db: Session = Depends(get_db),
    service: ObsidianIndexService = Depends(get_obsidian_index_service),
) -> dict:
    request = request or ObsidianIndexRequest()
    try:
        return service.index(db, relative_paths=request.relative_paths, force=request.force)
    except (ObsidianDisabledError, VaultConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/notes", response_model=ObsidianNoteListResponse)
def list_notes(
    status: str | None = Query(default=None),
    path_prefix: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    db: Session = Depends(get_db),
    service: ObsidianNoteService = Depends(get_obsidian_note_service),
) -> dict:
    safe_prefix = _safe_relative_prefix(path_prefix)
    notes = service.list_notes(db, status=status, path_prefix=safe_prefix, tag=tag)
    return {"total": len(notes), "items": notes}


@router.get("/notes/{note_id}", response_model=ObsidianNoteOut)
def get_note(
    note_id: str,
    db: Session = Depends(get_db),
    service: ObsidianNoteService = Depends(get_obsidian_note_service),
) -> object:
    try:
        return service.get_note(db, note_id)
    except ObsidianNoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/notes/{note_id}/chunks", response_model=ObsidianChunkListResponse)
def list_chunks(
    note_id: str,
    db: Session = Depends(get_db),
    service: ObsidianNoteService = Depends(get_obsidian_note_service),
) -> dict:
    try:
        chunks = service.list_chunks(db, note_id, VectorService(ensure_collection=False))
    except ObsidianNoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Qdrant is temporarily unavailable") from exc
    return {"total": len(chunks), "items": chunks}


@router.post("/notes/{note_id}/reindex", response_model=ObsidianOperationResponse)
def reindex_note(
    note_id: str,
    db: Session = Depends(get_db),
    service: ObsidianIndexService = Depends(get_obsidian_index_service),
) -> dict:
    try:
        return service.reindex_note(db, note_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ObsidianDisabledError, VaultConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_relative_prefix(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.replace("\\", "/").strip("/")
    first = normalized.split("/", 1)[0]
    if value.startswith(("/", "\\")) or ":" in first or ".." in normalized.split("/"):
        raise HTTPException(status_code=422, detail="path_prefix must be Vault-relative")
    return normalized
