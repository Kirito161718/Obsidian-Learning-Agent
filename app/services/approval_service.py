from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import threading

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ApprovalRequest, UpdateProposal
from app.official_sources.errors import official_error
from app.security.redaction import safe_text_preview, stable_hash


class ApprovalService:
    _local_locks: dict[str, threading.Lock] = {}
    _local_locks_guard = threading.Lock()

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create_for_proposal(self, db: Session, proposal_id: str) -> ApprovalRequest:
        proposal = db.get(UpdateProposal, proposal_id)
        if proposal is None or not proposal.payload_hash:
            raise official_error("official_proposal_invalid", "Update proposal is unavailable", status_code=404)
        if proposal.approval_request_id:
            existing = db.get(ApprovalRequest, proposal.approval_request_id)
            if existing is not None:
                return existing
        if proposal.status not in {"ready_for_review", "pending_approval"}:
            raise official_error("official_proposal_invalid", "Update proposal is not reviewable", status_code=409)
        key = f"official:{stable_hash({'proposal_id': proposal.id, 'payload_hash': proposal.payload_hash})}"
        existing = db.scalar(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == key))
        if existing:
            proposal.approval_request_id = existing.id
            proposal.status = "pending_approval"
            db.commit()
            return existing
        approval = ApprovalRequest(
            operation_type="apply_official_update_proposal",
            target_type="update_proposal",
            target_id=proposal.id,
            risk_level="HIGH_RISK",
            status="pending",
            payload_hash=proposal.payload_hash,
            summary=proposal.summary,
            idempotency_key=key,
            expires_at=datetime.now(UTC) + timedelta(hours=self.settings.approval_expires_hours),
        )
        db.add(approval)
        db.flush()
        proposal.approval_request_id = approval.id
        proposal.status = "pending_approval"
        db.commit()
        db.refresh(approval)
        return approval

    def approve(
        self,
        db: Session,
        proposal_id: str,
        *,
        payload_hash: str,
        confirm_all: bool,
        confirmation_text: str,
    ) -> tuple[UpdateProposal, ApprovalRequest]:
        proposal, approval = self._locked(db, proposal_id)
        if proposal.status in {"applied", "applied_with_index_error"} and approval.status == "consumed":
            return proposal, approval
        if proposal.status in {"rejected", "stale", "failed"}:
            raise official_error("official_proposal_invalid", "Update proposal can no longer be approved", status_code=409)
        self._validate_hash(proposal, approval, payload_hash)
        self._validate_expiry(db, approval)
        if not confirm_all:
            raise official_error("official_approval_required", "All proposal items must be confirmed")
        if confirmation_text != f"APPLY {proposal.id[:8]}":
            raise official_error("official_approval_required", "Approval confirmation text does not match")
        if approval.status == "consumed":
            raise official_error("official_approval_consumed", "Approval has already been consumed", status_code=409)
        if approval.status == "rejected":
            raise official_error("official_proposal_invalid", "Rejected approval cannot be reused", status_code=409)
        approval.status = "approved"
        approval.decided_at = approval.decided_at or datetime.now(UTC)
        proposal.status = "approved"
        proposal.approved_at = proposal.approved_at or datetime.now(UTC)
        db.commit()
        return proposal, approval

    def reject(self, db: Session, proposal_id: str, *, payload_hash: str, reason: str | None) -> tuple[UpdateProposal, ApprovalRequest]:
        proposal, approval = self._locked(db, proposal_id)
        if proposal.status == "rejected" and approval.status == "rejected":
            return proposal, approval
        if proposal.status in {"applied", "applied_with_index_error", "stale", "failed"}:
            raise official_error("official_proposal_invalid", "Update proposal can no longer be rejected", status_code=409)
        self._validate_hash(proposal, approval, payload_hash)
        self._validate_expiry(db, approval)
        if approval.status == "approved" or proposal.status == "approved":
            raise official_error("official_proposal_invalid", "An approved decision cannot be changed", status_code=409)
        approval.status = "rejected"
        approval.decided_at = datetime.now(UTC)
        approval.decision_note = safe_text_preview(reason, 1000) if reason else None
        proposal.status = "rejected"
        proposal.rejected_at = datetime.now(UTC)
        db.commit()
        return proposal, approval

    def get(self, db: Session, approval_id: str) -> ApprovalRequest:
        row = db.get(ApprovalRequest, approval_id)
        if row is None:
            raise official_error("official_approval_required", "Approval request was not found", status_code=404)
        self._expire_if_needed(db, row)
        return row

    def list(self, db: Session) -> list[ApprovalRequest]:
        rows = list(db.scalars(select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())).all())
        for row in rows:
            self._expire_if_needed(db, row)
        return rows

    def consume(self, db: Session, approval: ApprovalRequest) -> None:
        if approval.status == "consumed":
            return
        if approval.status != "approved":
            raise official_error("official_approval_required", "Approval has not been granted", status_code=409)
        approval.status = "consumed"
        approval.consumed_at = datetime.now(UTC)

    @contextmanager
    def execution_lock(self, db: Session, proposal_id: str) -> Iterator[None]:
        """Serialize a decision plus graph resume across workers.

        MySQL named locks are connection-scoped, so a crashed worker releases the
        claim when its connection closes and a later request can resume from the
        persistent checkpoint. Tests and non-MySQL development use the same
        fail-fast semantics with a process-local lock.
        """
        lock_name = f"official-approval:{stable_hash({'proposal_id': proposal_id})[:32]}"
        bind = db.get_bind()
        if bind.dialect.name == "mysql":
            with bind.connect() as connection:
                acquired = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": lock_name}).scalar()
                if acquired != 1:
                    raise official_error(
                        "official_approval_required",
                        "Another approval decision is already executing",
                        status_code=409,
                    )
                try:
                    yield
                finally:
                    connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
            return

        with self._local_locks_guard:
            local_lock = self._local_locks.setdefault(lock_name, threading.Lock())
        if not local_lock.acquire(blocking=False):
            raise official_error(
                "official_approval_required",
                "Another approval decision is already executing",
                status_code=409,
            )
        try:
            yield
        finally:
            local_lock.release()
            with self._local_locks_guard:
                if not local_lock.locked():
                    self._local_locks.pop(lock_name, None)

    def _locked(self, db: Session, proposal_id: str) -> tuple[UpdateProposal, ApprovalRequest]:
        proposal = db.scalar(select(UpdateProposal).where(UpdateProposal.id == proposal_id).with_for_update())
        if proposal is None or not proposal.approval_request_id:
            raise official_error("official_approval_required", "Approval request is unavailable", status_code=404)
        approval = db.scalar(
            select(ApprovalRequest).where(ApprovalRequest.id == proposal.approval_request_id).with_for_update()
        )
        if approval is None:
            raise official_error("official_approval_required", "Approval request is unavailable", status_code=404)
        return proposal, approval

    @staticmethod
    def _validate_hash(proposal: UpdateProposal, approval: ApprovalRequest, payload_hash: str) -> None:
        if not proposal.payload_hash or payload_hash != proposal.payload_hash or payload_hash != approval.payload_hash:
            raise official_error("official_approval_hash_mismatch", "Approval payload hash does not match", status_code=409)

    def _validate_expiry(self, db: Session, approval: ApprovalRequest) -> None:
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC) or approval.status == "expired":
            approval.status = "expired"
            db.commit()
            raise official_error("official_approval_expired", "Approval request has expired", status_code=409)

    def _expire_if_needed(self, db: Session, approval: ApprovalRequest) -> None:
        if approval.status != "pending":
            return
        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            approval.status = "expired"
            db.commit()
