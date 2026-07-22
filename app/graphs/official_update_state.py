from typing import Any, TypedDict

from sqlalchemy.orm import Session


class OfficialUpdateState(TypedDict, total=False):
    source_id: str
    snapshot_id: str
    proposal_id: str
    approval_request_id: str
    run_id: str
    note_ids: list[str]
    payload_hash: str
    status: str
    decision: str
    error_code: str
    item_count: int
    applied_count: int
    reindex_status: str


class OfficialUpdateContext(TypedDict):
    db: Session
    proposal_builder: Any
    approval_service: Any
    proposal_service: Any
