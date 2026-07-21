from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.official_sources.version_store import NoteVersionStore
from app.schemas.approvals import ApprovalDecisionRequest, RejectionRequest
from app.schemas.official_sources import (
    CleanupRequest,
    NoteLinkCreate,
    NoteLinkOut,
    OfficialSourceCreate,
    OfficialSourceOut,
    OfficialSourcePatch,
    SnapshotOut,
)
from app.services.approval_service import ApprovalService
from app.services.note_version_service import NoteVersionService
from app.services.official_audit_service import OfficialAuditService
from app.services.official_maintenance_service import OfficialMaintenanceService
from app.services.official_source_service import OfficialFetchService, OfficialSourceService
from app.services.update_proposal_service import UpdateProposalService

settings = get_settings()
templates = Jinja2Templates(directory=settings.template_dir)
router = APIRouter(prefix="/official", tags=["official-sources"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def official_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "official_sources.html")


@router.post("/sources", response_model=OfficialSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(payload: OfficialSourceCreate, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().create(db, payload)


@router.get("/sources", response_model=list[OfficialSourceOut])
def list_sources(db: Session = Depends(get_db)) -> list:
    return OfficialSourceService().list(db)


@router.get("/sources/{source_id}", response_model=OfficialSourceOut)
def get_source(source_id: str, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().get(db, source_id)


@router.patch("/sources/{source_id}", response_model=OfficialSourceOut)
def patch_source(source_id: str, payload: OfficialSourcePatch, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().update(db, source_id, payload)


@router.post("/sources/{source_id}/disable", response_model=OfficialSourceOut)
def disable_source(source_id: str, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().set_enabled(db, source_id, False)


@router.post("/sources/{source_id}/enable", response_model=OfficialSourceOut)
def enable_source(source_id: str, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().set_enabled(db, source_id, True)


@router.get("/sources/{source_id}/note-links", response_model=list[NoteLinkOut])
def list_note_links(source_id: str, db: Session = Depends(get_db)) -> list:
    return OfficialSourceService().list_links(db, source_id)


@router.post("/sources/{source_id}/note-links", response_model=NoteLinkOut, status_code=status.HTTP_201_CREATED)
def create_note_link(source_id: str, payload: NoteLinkCreate, db: Session = Depends(get_db)) -> object:
    return OfficialSourceService().add_link(db, source_id, payload)


@router.delete("/sources/{source_id}/note-links/{link_id}")
def delete_note_link(source_id: str, link_id: str, db: Session = Depends(get_db)) -> dict:
    OfficialSourceService().delete_link(db, source_id, link_id)
    return {"deleted": True, "link_id": link_id}


@router.post("/sources/{source_id}/fetch")
def fetch_source(source_id: str, db: Session = Depends(get_db)) -> dict:
    return OfficialFetchService().fetch_source(db, source_id)


@router.post("/sources/{source_id}/audit")
def audit_source(source_id: str, db: Session = Depends(get_db)) -> dict:
    return OfficialAuditService().audit(db, source_id)


@router.get("/sources/{source_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(source_id: str, db: Session = Depends(get_db)) -> list:
    return OfficialFetchService().list_snapshots(db, source_id)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: str, db: Session = Depends(get_db)) -> object:
    return OfficialFetchService().get_snapshot(db, snapshot_id)


@router.get("/snapshots/{snapshot_id}/preview")
def preview_snapshot(
    snapshot_id: str,
    max_chars: int = Query(default=4000, ge=1, le=10_000),
    db: Session = Depends(get_db),
) -> dict:
    snapshot = OfficialFetchService().get_snapshot(db, snapshot_id)
    return {"snapshot_id": snapshot.id, "content_hash": snapshot.content_hash, "preview": OfficialFetchService().preview(db, snapshot_id, max_chars)}


@router.get("/proposals")
def list_proposals(db: Session = Depends(get_db)) -> list[dict]:
    return [_proposal_row(row) for row in UpdateProposalService().list(db)]


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    service = UpdateProposalService()
    proposal = service.get(db, proposal_id)
    return {**_proposal_row(proposal), "items": [_item_row(item) for item in service.items(db, proposal_id)]}


@router.get("/proposals/{proposal_id}/items")
def get_proposal_items(proposal_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [_item_row(item) for item in UpdateProposalService().items(db, proposal_id)]


@router.get("/proposals/{proposal_id}/diff")
def get_proposal_diff(proposal_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return UpdateProposalService().read_diff(db, proposal_id)


@router.get("/proposals/{proposal_id}/checkpoint")
def get_proposal_checkpoint(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    return OfficialAuditService().pending_state(db, proposal_id)


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: str,
    payload: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
) -> dict:
    service = ApprovalService()
    with service.execution_lock(db, proposal_id):
        proposal, approval = service.approve(
            db,
            proposal_id,
            payload_hash=payload.payload_hash,
            confirm_all=payload.confirm_all,
            confirmation_text=payload.confirmation_text,
        )
        if proposal.status in {"applied", "applied_with_index_error"}:
            return {"proposal_id": proposal.id, "status": proposal.status, "approval_status": approval.status}
        result = OfficialAuditService().resume(db, proposal_id, decision="approve")
        return {**result, "approval_status": db.get(type(approval), approval.id).status}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: str,
    payload: RejectionRequest,
    db: Session = Depends(get_db),
) -> dict:
    service = ApprovalService()
    with service.execution_lock(db, proposal_id):
        proposal_service = UpdateProposalService()
        current = proposal_service.get(db, proposal_id)
        if current.status == "rejected" and current.approval_request_id:
            approval = service.get(db, current.approval_request_id)
            return {"proposal_id": current.id, "status": current.status, "approval_status": approval.status}
        proposal, approval = service.reject(
            db, proposal_id, payload_hash=payload.payload_hash, reason=payload.reason
        )
        if proposal.status == "rejected":
            result = OfficialAuditService().resume(db, proposal_id, decision="reject")
            return {**result, "approval_status": approval.status}
        return {"proposal_id": proposal.id, "status": proposal.status, "approval_status": approval.status}


@router.post("/proposals/{proposal_id}/retry-reindex")
def retry_reindex(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    return UpdateProposalService().retry_reindex(db, proposal_id)


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db)) -> list[dict]:
    return [_approval_row(row) for row in ApprovalService().list(db)]


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, db: Session = Depends(get_db)) -> dict:
    return _approval_row(ApprovalService().get(db, approval_id))


@router.get("/note-versions")
def list_versions(db: Session = Depends(get_db)) -> list[dict]:
    return [_version_row(row) for row in NoteVersionService().list(db)]


@router.get("/note-versions/{version_id}")
def get_version(version_id: str, db: Session = Depends(get_db)) -> dict:
    return _version_row(NoteVersionService().get(db, version_id))


@router.get("/note-versions/{version_id}/diff")
def get_version_diff(version_id: str, db: Session = Depends(get_db)) -> dict:
    row = NoteVersionService().get(db, version_id)
    path = NoteVersionStore(settings).resolve(row.diff_path, row.obsidian_note_id, ".diff")
    return {"version_id": row.id, "diff": path.read_text(encoding="utf-8")}


@router.post("/maintenance/cleanup")
def cleanup(payload: CleanupRequest, db: Session = Depends(get_db)) -> dict:
    return OfficialMaintenanceService().cleanup(db, dry_run=payload.dry_run)


def _proposal_row(row) -> dict:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "snapshot_id": row.snapshot_id,
        "previous_snapshot_id": row.previous_snapshot_id,
        "status": row.status,
        "payload_hash": row.payload_hash,
        "summary": row.summary,
        "item_count": row.item_count,
        "approval_request_id": row.approval_request_id,
        "model_name": row.model_name,
        "model_call_count": row.model_call_count,
        "created_at": row.created_at,
        "ready_at": row.ready_at,
        "applied_at": row.applied_at,
        "error_code": row.error_code,
        "error_preview": row.error_preview,
        "risk_level": "HIGH_RISK",
        "approval_scope": "all_or_nothing",
    }


def _item_row(row) -> dict:
    return {
        "id": row.id,
        "proposal_id": row.proposal_id,
        "obsidian_note_id": row.obsidian_note_id,
        "relative_path": row.relative_path,
        "base_note_hash": row.base_note_hash,
        "proposed_note_hash": row.proposed_note_hash,
        "change_ratio": row.change_ratio,
        "operation_count": row.operation_count,
        "operations": row.operation_manifest,
        "evidence": row.evidence_manifest,
        "status": row.status,
        "error_code": row.error_code,
    }


def _approval_row(row) -> dict:
    return {
        "id": row.id,
        "operation_type": row.operation_type,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "risk_level": row.risk_level,
        "status": row.status,
        "payload_hash": row.payload_hash,
        "summary": row.summary,
        "expires_at": row.expires_at,
        "decided_at": row.decided_at,
        "consumed_at": row.consumed_at,
        "decision_note": row.decision_note,
        "created_at": row.created_at,
    }


def _version_row(row) -> dict:
    return {
        "id": row.id,
        "obsidian_note_id": row.obsidian_note_id,
        "proposal_id": row.proposal_id,
        "relative_path": row.relative_path,
        "before_hash": row.before_hash,
        "after_hash": row.after_hash,
        "pinned": row.pinned,
        "created_at": row.created_at,
    }
