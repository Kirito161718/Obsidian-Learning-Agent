from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import AgentRun, OfficialSourceSnapshot, UpdateProposal
from app.graphs.official_checkpoint import MySQLCheckpointSaver
from app.graphs.official_update_graph import OfficialUpdateWorkflow
from app.official_sources.errors import OfficialSourceError, official_error
from app.official_sources.proposal_builder import ProposalBuilder
from app.security.redaction import safe_text_preview
from app.services.approval_service import ApprovalService
from app.services.official_source_service import OfficialFetchService
from app.services.update_proposal_service import UpdateProposalService


class OfficialAuditService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fetch_service: OfficialFetchService | None = None,
        proposal_builder: ProposalBuilder | None = None,
        approval_service: ApprovalService | None = None,
        proposal_service: UpdateProposalService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.fetch_service = fetch_service or OfficialFetchService(self.settings)
        self.proposal_builder = proposal_builder or ProposalBuilder(self.settings)
        self.approval_service = approval_service or ApprovalService(self.settings)
        self.proposal_service = proposal_service or UpdateProposalService(self.settings)

    def audit(self, db: Session, source_id: str) -> dict:
        self._ensure_checkpoint()
        fetched = self.fetch_service.fetch_source(db, source_id)
        snapshot_id = fetched.get("snapshot_id")
        if not snapshot_id:
            snapshot = db.scalar(
                select(OfficialSourceSnapshot)
                .where(OfficialSourceSnapshot.source_id == source_id)
                .order_by(OfficialSourceSnapshot.created_at.desc())
                .limit(1)
            )
            snapshot_id = snapshot.id if snapshot else None
        if not snapshot_id:
            raise official_error("official_content_empty", "Official source has no snapshot")
        existing = db.scalar(
            select(UpdateProposal)
            .where(UpdateProposal.source_id == source_id, UpdateProposal.snapshot_id == snapshot_id)
            .order_by(UpdateProposal.created_at.desc())
            .limit(1)
        )
        if existing and existing.status not in {"failed", "stale", "rejected"}:
            return {
                "run_id": fetched["run_id"],
                "changed": fetched["changed"],
                "snapshot_id": snapshot_id,
                "proposal_id": existing.id,
                "status": existing.status,
                "approval_request_id": existing.approval_request_id,
                "payload_hash": existing.payload_hash,
            }
        proposal_id = str(uuid4())
        saver, workflow = self._workflow(db)
        state = {
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "proposal_id": proposal_id,
            "status": "building",
        }
        config = {"configurable": {"thread_id": proposal_id}}
        try:
            result = workflow.graph.invoke(state, config=config, context=self._context(db))
        except Exception as exc:
            db.rollback()
            run = db.scalar(
                select(AgentRun)
                .where(AgentRun.official_proposal_id == proposal_id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
            if run and run.status == "running":
                run.status = "failed"
                run.error_code = getattr(exc, "code", "official_proposal_invalid")
                run.error_message_preview = safe_text_preview(type(exc).__name__, 500)
                run.finished_at = datetime.now(UTC)
                started = run.started_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                run.duration_ms = max(0, int((run.finished_at - started).total_seconds() * 1000))
                db.commit()
            if isinstance(exc, OfficialSourceError):
                raise
            raise official_error(
                "official_proposal_invalid",
                "Official update proposal could not be built",
                status_code=502,
            ) from exc
        proposal = db.get(UpdateProposal, proposal_id)
        if proposal is None:
            saver.delete_thread(proposal_id)
            return {
                "run_id": result.get("run_id") or fetched["run_id"],
                "changed": fetched["changed"],
                "snapshot_id": snapshot_id,
                "proposal_id": None,
                "status": "no_update_needed",
            }
        return {
            "run_id": result.get("run_id") or fetched["run_id"],
            "changed": fetched["changed"],
            "snapshot_id": snapshot_id,
            "proposal_id": proposal.id,
            "status": proposal.status,
            "approval_request_id": proposal.approval_request_id,
            "payload_hash": proposal.payload_hash,
        }

    def resume(self, db: Session, proposal_id: str, *, decision: str) -> dict:
        self._ensure_checkpoint()
        proposal = db.get(UpdateProposal, proposal_id)
        if proposal is None or not proposal.approval_request_id or not proposal.payload_hash:
            raise official_error("official_proposal_invalid", "Update proposal is unavailable", status_code=404)
        _, workflow = self._workflow(db)
        config = {"configurable": {"thread_id": proposal.id}}
        result = workflow.graph.invoke(
            Command(
                resume={
                    "decision": decision,
                    "approval_id": proposal.approval_request_id,
                    "payload_hash": proposal.payload_hash,
                }
            ),
            config=config,
            context=self._context(db),
        )
        db.expire_all()
        proposal = db.get(UpdateProposal, proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": proposal.status if proposal else result.get("status"),
            "applied_count": int(result.get("applied_count") or 0),
            "reindex_status": result.get("reindex_status"),
        }

    def pending_state(self, db: Session, proposal_id: str) -> dict:
        self._ensure_checkpoint()
        saver, workflow = self._workflow(db)
        state = workflow.graph.get_state({"configurable": {"thread_id": proposal_id}})
        return {
            "proposal_id": proposal_id,
            "next": list(state.next),
            "has_checkpoint": bool(state.config),
            "status": (state.values or {}).get("status"),
        }

    def _workflow(self, db: Session):
        saver = MySQLCheckpointSaver(db.get_bind())
        return saver, OfficialUpdateWorkflow(saver)

    def _context(self, db: Session) -> dict:
        return {
            "db": db,
            "proposal_builder": self.proposal_builder,
            "approval_service": self.approval_service,
            "proposal_service": self.proposal_service,
        }

    def _ensure_checkpoint(self) -> None:
        if not self.settings.official_checkpoint_enabled:
            raise official_error("official_checkpoint_unavailable", "Persistent official checkpointing is disabled", status_code=503)
