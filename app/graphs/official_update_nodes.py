from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy import select

from app.db.models import AgentRun, OfficialSource, OfficialSourceSnapshot, UpdateProposal, UpdateProposalItem
from app.graphs.official_update_state import OfficialUpdateContext, OfficialUpdateState
from app.official_sources.errors import official_error


class OfficialUpdateNodes:
    def initialize_official_audit(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        if state.get("run_id"):
            return {"status": "building"}
        db = runtime.context["db"]
        now = datetime.now(UTC)
        run = AgentRun(
            run_type="official_audit",
            status="running",
            official_source_id=state["source_id"],
            official_proposal_id=state["proposal_id"],
            official_snapshot_changed=True,
            mode="manual",
            started_at=now,
            created_at=now,
        )
        db.add(run)
        db.commit()
        return {"run_id": run.id, "status": "building"}

    def persist_proposal(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        db = runtime.context["db"]
        proposal = runtime.context["proposal_builder"].build(
            db,
            proposal_id=state["proposal_id"],
            source_id=state["source_id"],
            snapshot_id=state["snapshot_id"],
        )
        run = db.get(AgentRun, state["run_id"])
        if proposal is None:
            if run:
                self._finish_run(run, status="succeeded", item_count=0)
                db.commit()
            return {"status": "no_update_needed", "item_count": 0}
        note_ids = list(
            db.scalars(
                select(UpdateProposalItem.obsidian_note_id).where(UpdateProposalItem.proposal_id == proposal.id)
            ).all()
        )
        if run:
            run.linked_note_count = len(note_ids)
            run.proposal_item_count = proposal.item_count
            run.model_call_count = proposal.model_call_count
            db.commit()
        return {
            "status": "ready_for_review",
            "payload_hash": proposal.payload_hash or "",
            "item_count": proposal.item_count,
            "note_ids": note_ids,
        }

    @staticmethod
    def route_proposal(state: OfficialUpdateState) -> Literal["approval", "done"]:
        return "done" if state.get("status") == "no_update_needed" else "approval"

    def create_approval_request(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        db = runtime.context["db"]
        approval = runtime.context["approval_service"].create_for_proposal(db, state["proposal_id"])
        return {"approval_request_id": approval.id, "status": "pending_approval"}

    def interrupt_for_approval(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        db = runtime.context["db"]
        proposal = db.get(UpdateProposal, state["proposal_id"])
        if proposal is None:
            raise official_error("official_proposal_invalid", "Update proposal is unavailable", status_code=404)
        snapshot = db.get(OfficialSourceSnapshot, proposal.snapshot_id)
        source = db.get(OfficialSource, proposal.source_id)
        items = list(
            db.scalars(
                select(UpdateProposalItem).where(UpdateProposalItem.proposal_id == proposal.id)
            ).all()
        )
        decision = interrupt(
            {
                "proposal_id": proposal.id,
                "source_id": proposal.source_id,
                "source_name": source.name if source else None,
                "snapshot_id": proposal.snapshot_id,
                "snapshot_hash": snapshot.content_hash if snapshot else None,
                "item_count": proposal.item_count,
                "summary": proposal.summary,
                "items": [
                    {
                        "relative_path": item.relative_path,
                        "change_ratio": item.change_ratio,
                        "evidence": item.evidence_manifest,
                        "diff_ref": item.diff_path,
                    }
                    for item in items
                ],
                "risk_level": "HIGH_RISK",
                "approval_scope": "all_or_nothing",
                "payload_hash": proposal.payload_hash,
            }
        )
        if not isinstance(decision, dict) or decision.get("decision") not in {"approve", "reject"}:
            raise official_error("official_approval_required", "Approval resume payload is invalid", status_code=409)
        if decision.get("approval_id") != state.get("approval_request_id"):
            raise official_error("official_approval_required", "Approval resume identity does not match", status_code=409)
        if decision.get("payload_hash") != state.get("payload_hash"):
            raise official_error("official_approval_hash_mismatch", "Approval resume hash does not match", status_code=409)
        return {"decision": decision["decision"], "status": "approved" if decision["decision"] == "approve" else "rejected"}

    @staticmethod
    def route_decision(state: OfficialUpdateState) -> Literal["apply", "reject"]:
        return "apply" if state.get("decision") == "approve" else "reject"

    def apply_note_batch(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        db = runtime.context["db"]
        result = runtime.context["proposal_service"].apply(
            db,
            proposal_id=state["proposal_id"],
            approval_id=state["approval_request_id"],
            payload_hash=state["payload_hash"],
        )
        run = db.get(AgentRun, state["run_id"])
        if run:
            self._finish_run(
                run,
                status="succeeded",
                item_count=state.get("item_count", 0),
                applied_count=int(result.get("applied_count") or 0),
                approval_status="consumed",
                reindex_status="failed" if result.get("status") == "applied_with_index_error" else "succeeded",
            )
            db.commit()
        return {
            "status": str(result["status"]),
            "applied_count": int(result.get("applied_count") or 0),
            "reindex_status": "failed" if result.get("status") == "applied_with_index_error" else "succeeded",
        }

    def finalize_rejected(
        self, state: OfficialUpdateState, runtime: Runtime[OfficialUpdateContext]
    ) -> OfficialUpdateState:
        db = runtime.context["db"]
        run = db.get(AgentRun, state["run_id"])
        if run:
            self._finish_run(run, status="succeeded", item_count=state.get("item_count", 0), approval_status="rejected")
            db.commit()
        return {"status": "rejected", "applied_count": 0}

    @staticmethod
    def _finish_run(
        run: AgentRun,
        *,
        status: str,
        item_count: int = 0,
        applied_count: int = 0,
        approval_status: str | None = None,
        reindex_status: str | None = None,
    ) -> None:
        finished = datetime.now(UTC)
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        run.status = status
        run.proposal_item_count = item_count
        run.applied_count = applied_count
        run.approval_status = approval_status
        run.reindex_status = reindex_status
        run.finished_at = finished
        run.duration_ms = max(0, int((finished - started).total_seconds() * 1000))
