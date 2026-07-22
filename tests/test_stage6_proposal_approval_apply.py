import hashlib
import os
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, Settings
from app.db.models import (
    AgentRun,
    ApprovalRequest,
    NoteVersion,
    ObsidianNote,
    OfficialCheckpoint,
    OfficialSource,
    OfficialSourceNoteLink,
    OfficialSourceSnapshot,
    UpdateProposal,
    UpdateProposalItem,
)
from app.graphs.official_checkpoint import MySQLCheckpointSaver
from app.official_sources.errors import OfficialSourceError
from app.official_sources.fetcher import FetchResult
from app.official_sources.normalizer import build_section_manifest, sections_with_content
from app.official_sources.note_patcher import NotePatchService
from app.official_sources.proposal_builder import ProposalBuilder
from app.official_sources.proposal_validator import EditOperation, ProposalValidator
from app.official_sources.snapshot_store import SnapshotStore
from app.official_sources.url_security import URLSecurityValidator
from app.schemas.official_sources import NoteLinkCreate, OfficialSourceCreate, OfficialSourcePatch
from app.services.approval_service import ApprovalService
from app.services.llm_service import OFFICIAL_UPDATE_SYSTEM_PROMPT
from app.services.official_audit_service import OfficialAuditService
from app.services.official_maintenance_service import OfficialMaintenanceService
from app.services.official_source_service import OfficialSourceService
from app.services.official_source_service import OfficialFetchService
from app.services.update_proposal_service import UpdateProposalService
from app.tools.executor import ToolExecutionError, ToolExecutor
from app.tools.registry import build_tool_registry


def test_official_llm_prompt_requires_structured_evidence_objects():
    assert '"evidence_refs":[{"snapshot_section_id"' in OFFICIAL_UPDATE_SYSTEM_PROMPT
    assert "绝不能只返回 section_id 字符串" in OFFICIAL_UPDATE_SYSTEM_PROMPT


@pytest.fixture()
def stage6_settings(tmp_path, monkeypatch):
    runtime_root = BASE_DIR / "runtime" / "pytest-stage6" / tmp_path.name
    monkeypatch.setattr(Settings, "official_snapshot_dir", property(lambda self: runtime_root / "snapshots"))
    monkeypatch.setattr(Settings, "update_proposal_dir", property(lambda self: runtime_root / "proposals"))
    monkeypatch.setattr(Settings, "note_version_dir", property(lambda self: runtime_root / "versions"))
    monkeypatch.setattr(Settings, "official_temp_dir", property(lambda self: runtime_root / "temp"))
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(
        _env_file=None,
        app_env="test",
        obsidian_enabled=True,
        obsidian_vault_path=vault,
        qdrant_collection=f"stage6-{tmp_path.name}",
    )
    yield settings
    shutil.rmtree(runtime_root, ignore_errors=True)


class MockLLM:
    model = "mock-official"

    def __init__(self, plans):
        self.plans = list(plans) if isinstance(plans, list) else [plans]
        self.calls = 0

    def generate_official_update_plan(self, payload):
        self.calls += 1
        plan = self.plans[min(self.calls - 1, len(self.plans) - 1)]
        return plan(payload) if callable(plan) else plan


class FakeIndexService:
    def __init__(self, fail=False):
        self.fail = fail
        self.note_ids = []

    def reindex_note(self, db, note_id):
        del db
        self.note_ids.append(note_id)
        if self.fail:
            return {"counts": {"error": 1}, "items": [{"kind": "error"}]}
        return {"counts": {"indexed": 1}, "items": [{"kind": "changed"}]}


def _raw_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_source_snapshot_note(db, settings, *, source_text="# Facts\n\nVersion: 2\n", note_text="---\ntitle: Test\n---\n# Facts\n\nVersion: 1\n", name="note.md"):
    source = OfficialSource(
        name="Official",
        url="https://example.com/docs",
        canonical_url="https://example.com/docs",
        allowed_host="example.com",
        source_type="markdown",
        enabled=True,
        status="active",
    )
    db.add(source); db.flush()
    snapshot_id = str(uuid4())
    storage_path = SnapshotStore(settings).save(source.id, snapshot_id, source_text)
    snapshot = OfficialSourceSnapshot(
        id=snapshot_id,
        source_id=source.id,
        content_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        storage_path=storage_path,
        normalized_format="markdown",
        normalizer_version="official-v1",
        http_status=200,
        content_type="text/markdown",
        size_bytes=len(source_text.encode()),
        section_manifest=build_section_manifest(source_text),
    )
    path = settings.obsidian_vault_path / name
    path.write_text(note_text, encoding="utf-8", newline="")
    note = ObsidianNote(
        relative_path=name,
        file_name=name,
        title="Test",
        observed_hash=_raw_hash(path),
        indexed_hash=_raw_hash(path),
        size_bytes=path.stat().st_size,
        index_status="indexed",
    )
    db.add_all([snapshot, note]); db.flush()
    link = OfficialSourceNoteLink(source_id=source.id, obsidian_note_id=note.id, target_heading_paths=[["Facts"]], enabled=True)
    db.add(link); db.commit()
    return source, snapshot, note, path


def update_plan(payload):
    section = payload["snapshot"]["sections"][0]
    return {
        "summary": "Update documented version",
        "items": [
            {
                "note_id": payload["notes"][0]["note_id"],
                "operations": [
                    {
                        "operation_type": "replace",
                        "target_heading_path": ["Facts"],
                        "target_excerpt": "Version: 1",
                        "replacement_markdown": "Version: 2",
                        "rationale": "Official version changed",
                        "evidence_refs": [
                            {"snapshot_section_id": section["section_id"], "evidence_excerpt": "Version: 2"}
                        ],
                    }
                ],
            }
        ],
    }


def build_proposal(db, settings, source, snapshot, plan=update_plan):
    proposal_id = str(uuid4())
    proposal = ProposalBuilder(settings, llm_client=MockLLM(plan)).build(
        db, proposal_id=proposal_id, source_id=source.id, snapshot_id=snapshot.id
    )
    assert proposal is not None
    return proposal


def approve_proposal(db, settings, proposal):
    service = ApprovalService(settings)
    approval = service.create_for_proposal(db, proposal.id)
    proposal, approval = service.approve(
        db,
        proposal.id,
        payload_hash=proposal.payload_hash,
        confirm_all=True,
        confirmation_text=f"APPLY {proposal.id[:8]}",
    )
    return proposal, approval


def test_source_registration_derives_host_and_rejects_client_only_fields(db_session, stage6_settings):
    validator = URLSecurityValidator(stage6_settings, resolver=lambda host, port: ["93.184.216.34"])
    service = OfficialSourceService(stage6_settings, validator)
    source = service.create(
        db_session,
        OfficialSourceCreate(name="Docs", url="https://EXAMPLE.com/docs#x", source_type="html"),
    )
    assert source.allowed_host == "example.com" and source.canonical_url == "https://example.com/docs"
    service.update(db_session, source.id, OfficialSourcePatch(name="Docs 2", enabled=False))
    assert source.status == "disabled"


def test_note_link_requires_existing_safe_indexed_markdown(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    db_session.delete(db_session.scalar(select(OfficialSourceNoteLink))); db_session.commit()
    link = OfficialSourceService(stage6_settings).add_link(
        db_session, source.id, NoteLinkCreate(obsidian_note_id=note.id, target_heading_paths=[["Facts"]])
    )
    assert link.obsidian_note_id == note.id and path.read_text(encoding="utf-8").endswith("Version: 1\n")
    with pytest.raises(OfficialSourceError):
        OfficialSourceService(stage6_settings).add_link(
            db_session, source.id, NoteLinkCreate(obsidian_note_id="missing", target_heading_paths=[])
        )


@pytest.mark.parametrize("operation_type", ["replace", "insert_before", "insert_after", "append_section"])
def test_note_patcher_supports_only_structured_operations_and_preserves_frontmatter(tmp_path, operation_type):
    path = tmp_path / "note.md"
    path.write_bytes(b"\xef\xbb\xbf---\r\ntitle: T\r\n---\r\n# Facts\r\n\r\nOld\r\n")
    patcher = NotePatchService(); document = patcher.read(path)
    operation = {
        "operation_type": operation_type,
        "target_heading_path": ["Facts"],
        "target_excerpt": "Old",
        "replacement_markdown": "New",
    }
    result = patcher.apply(document, [operation])
    assert result.content.startswith(b"\xef\xbb\xbf---\r\ntitle: T\r\n---")
    assert b"\r\n" in result.content and result.content_hash == hashlib.sha256(result.content).hexdigest()


def test_note_patcher_rejects_zero_and_ambiguous_targets(tmp_path):
    path = tmp_path / "note.md"; path.write_text("# Facts\nOld Old\n", encoding="utf-8")
    patcher = NotePatchService(); document = patcher.read(path)
    operation = {"operation_type": "replace", "target_heading_path": ["Facts"], "target_excerpt": "Missing", "replacement_markdown": "New"}
    with pytest.raises(OfficialSourceError) as missing:
        patcher.apply(document, [operation])
    assert missing.value.code == "official_proposal_stale"
    operation["target_excerpt"] = "Old"
    with pytest.raises(OfficialSourceError) as ambiguous:
        patcher.apply(document, [operation])
    assert ambiguous.value.code == "official_proposal_invalid"


def test_note_patcher_rejects_frontmatter_and_broken_code_fence(tmp_path):
    path = tmp_path / "note.md"; path.write_text("---\ntitle: T\n---\n# F\nOld\n", encoding="utf-8")
    document = NotePatchService().read(path)
    with pytest.raises(OfficialSourceError):
        NotePatchService().apply(document, [{"operation_type": "replace", "target_heading_path": [], "target_excerpt": "title: T", "replacement_markdown": "title: X"}])
    with pytest.raises(OfficialSourceError):
        NotePatchService().apply(document, [{"operation_type": "replace", "target_heading_path": ["F"], "target_excerpt": "Old", "replacement_markdown": "```python\nprint(1)"}])


def test_proposal_validator_requires_real_evidence_and_linked_scope(stage6_settings):
    validator = ProposalValidator(stage6_settings)
    base = {
        "operation_type": "replace",
        "target_heading_path": ["Facts"],
        "target_excerpt": "Old",
        "replacement_markdown": "New",
        "rationale": "Fact changed",
        "evidence_refs": [{"snapshot_section_id": "section-a", "evidence_excerpt": "Official"}],
    }
    operation = EditOperation.model_validate(base)
    assert validator.validate_operations([operation], sections={"section-a": "Official fact"}, allowed_heading_paths=[["Facts"]])
    for mutation in (
        {"snapshot_section_id": "missing", "evidence_excerpt": "Official"},
        {"snapshot_section_id": "section-a", "evidence_excerpt": "Absent"},
    ):
        invalid = EditOperation.model_validate({**base, "evidence_refs": [mutation]})
        with pytest.raises(OfficialSourceError) as caught:
            validator.validate_operations([invalid], sections={"section-a": "Official fact"}, allowed_heading_paths=[["Facts"]])
        assert caught.value.code == "official_evidence_invalid"
    outside = EditOperation.model_validate({**base, "target_heading_path": ["Other"]})
    with pytest.raises(OfficialSourceError):
        validator.validate_operations([outside], sections={"section-a": "Official fact"}, allowed_heading_paths=[["Facts"]])


def test_proposal_builder_persists_artifacts_hash_and_no_full_body_in_database(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    item = db_session.scalar(select(UpdateProposalItem).where(UpdateProposalItem.proposal_id == proposal.id))
    assert proposal.status == "ready_for_review" and proposal.item_count == 1 and len(proposal.payload_hash) == 64
    assert not Path(item.proposed_content_path).is_absolute() and not Path(item.diff_path).is_absolute()
    assert "Version: 2" in (BASE_DIR / item.proposed_content_path).read_text(encoding="utf-8")
    database_values = " ".join(str(value) for value in item.__dict__.values())
    assert "title: Test" not in database_values and path.read_text(encoding="utf-8").endswith("Version: 1\n")


def test_proposal_payload_hash_changes_when_operation_changes(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    first = build_proposal(db_session, stage6_settings, source, snapshot)
    db_session.delete(first); db_session.commit()
    shutil.rmtree(stage6_settings.update_proposal_dir / first.id, ignore_errors=True)
    def changed(payload):
        result = update_plan(payload); result["summary"] = "Different summary"; return result
    second = build_proposal(db_session, stage6_settings, source, snapshot, changed)
    assert first.payload_hash != second.payload_hash


@pytest.mark.parametrize(
    "case",
    ["wrong_hash", "confirm_false", "wrong_text", "expired", "rejected"],
)
def test_approval_rejects_invalid_or_expired_decisions(db_session, stage6_settings, case):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    service = ApprovalService(stage6_settings); approval = service.create_for_proposal(db_session, proposal.id)
    if case == "expired":
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1); db_session.commit()
    if case == "rejected":
        service.reject(db_session, proposal.id, payload_hash=proposal.payload_hash, reason="no")
    kwargs = {"payload_hash": proposal.payload_hash, "confirm_all": True, "confirmation_text": f"APPLY {proposal.id[:8]}"}
    if case == "wrong_hash": kwargs["payload_hash"] = "0" * 64
    if case == "confirm_false": kwargs["confirm_all"] = False
    if case == "wrong_text": kwargs["confirmation_text"] = "APPLY wrong"
    with pytest.raises(OfficialSourceError):
        service.approve(db_session, proposal.id, **kwargs)


def test_approval_is_high_risk_all_or_nothing_and_bound_to_payload(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    proposal, approval = approve_proposal(db_session, stage6_settings, proposal)
    assert proposal.status == "approved" and approval.status == "approved"
    assert approval.risk_level == "HIGH_RISK" and approval.payload_hash == proposal.payload_hash
    assert approval.operation_type == "apply_official_update_proposal"


def test_apply_creates_before_version_preserves_frontmatter_and_is_idempotent(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    before = path.read_bytes(); proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    proposal, approval = approve_proposal(db_session, stage6_settings, proposal)
    index = FakeIndexService()
    service = UpdateProposalService(stage6_settings, index_service=index)
    result = service.apply(db_session, proposal_id=proposal.id, approval_id=approval.id, payload_hash=proposal.payload_hash)
    assert result["status"] == "applied" and "Version: 2" in path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").startswith("---\ntitle: Test\n---")
    version = db_session.scalar(select(NoteVersion).where(NoteVersion.proposal_id == proposal.id))
    assert (BASE_DIR / version.backup_path).read_bytes() == before
    assert version.after_hash == _raw_hash(path) and index.note_ids == [note.id]
    again = service.apply(db_session, proposal_id=proposal.id, approval_id=approval.id, payload_hash=proposal.payload_hash)
    assert again["status"] == "applied" and db_session.query(NoteVersion).count() == 1 and index.note_ids == [note.id]


def test_note_change_before_approval_marks_entire_proposal_stale_without_version_or_reindex(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    proposal, approval = approve_proposal(db_session, stage6_settings, proposal)
    path.write_text(path.read_text(encoding="utf-8") + "User edit\n", encoding="utf-8")
    index = FakeIndexService()
    result = UpdateProposalService(stage6_settings, index_service=index).apply(
        db_session, proposal_id=proposal.id, approval_id=approval.id, payload_hash=proposal.payload_hash
    )
    assert result["status"] == "stale" and "User edit" in path.read_text(encoding="utf-8")
    assert db_session.query(NoteVersion).count() == 0 and index.note_ids == []
    assert db_session.get(ApprovalRequest, approval.id).status == "consumed"


def test_multi_file_failure_rolls_back_already_replaced_note(db_session, stage6_settings):
    source, snapshot, note1, path1 = seed_source_snapshot_note(db_session, stage6_settings, name="one.md")
    note2_text = "---\ntitle: Two\n---\n# Facts\n\nVersion: 1\n"
    path2 = stage6_settings.obsidian_vault_path / "two.md"; path2.write_text(note2_text, encoding="utf-8")
    note2 = ObsidianNote(relative_path="two.md", file_name="two.md", title="Two", observed_hash=_raw_hash(path2), indexed_hash=_raw_hash(path2), size_bytes=path2.stat().st_size, index_status="indexed")
    db_session.add(note2); db_session.flush(); db_session.add(OfficialSourceNoteLink(source_id=source.id, obsidian_note_id=note2.id, target_heading_paths=[["Facts"]], enabled=True)); db_session.commit()
    def two_note_plan(payload):
        section = payload["snapshot"]["sections"][0]
        items=[]
        for note in payload["notes"]:
            items.append({"note_id":note["note_id"],"operations":[{"operation_type":"replace","target_heading_path":["Facts"],"target_excerpt":"Version: 1","replacement_markdown":"Version: 2","rationale":"Changed","evidence_refs":[{"snapshot_section_id":section["section_id"],"evidence_excerpt":"Version: 2"}]}]})
        return {"summary":"two", "items":items}
    before1, before2 = path1.read_bytes(), path2.read_bytes()
    proposal = build_proposal(db_session, stage6_settings, source, snapshot, two_note_plan)
    proposal, approval = approve_proposal(db_session, stage6_settings, proposal)
    calls = {"count": 0}
    def fail_second(source_path, target_path):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("fault injection")
        os.replace(source_path, target_path)
    with pytest.raises(OfficialSourceError) as caught:
        UpdateProposalService(stage6_settings, index_service=FakeIndexService(), replace_file=fail_second).apply(
            db_session, proposal_id=proposal.id, approval_id=approval.id, payload_hash=proposal.payload_hash
        )
    assert caught.value.code == "official_atomic_write_failed"
    assert path1.read_bytes() == before1 and path2.read_bytes() == before2
    assert not list(stage6_settings.obsidian_vault_path.glob("*.tmp"))


def test_reindex_failure_is_retryable_without_rewriting_note(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot); proposal, approval = approve_proposal(db_session, stage6_settings, proposal)
    failing = UpdateProposalService(stage6_settings, index_service=FakeIndexService(fail=True))
    result = failing.apply(db_session, proposal_id=proposal.id, approval_id=approval.id, payload_hash=proposal.payload_hash)
    applied_hash = _raw_hash(path)
    assert result["status"] == "applied_with_index_error"
    retry_index = FakeIndexService(); retry = UpdateProposalService(stage6_settings, index_service=retry_index).retry_reindex(db_session, proposal.id)
    assert retry["status"] == "applied" and _raw_hash(path) == applied_hash and db_session.query(NoteVersion).count() == 1


def test_checkpoint_saver_resumes_interrupt_after_new_instance(db_session):
    class State(dict):
        pass
    from typing import TypedDict
    class GraphState(TypedDict, total=False):
        value: int
        approved: bool
    def approval_node(state):
        return {"approved": interrupt({"value": state["value"]})}
    builder = StateGraph(GraphState); builder.add_node("approval", approval_node); builder.add_edge(START, "approval"); builder.add_edge("approval", END)
    config = {"configurable": {"thread_id": "proposal-thread"}}
    first = builder.compile(checkpointer=MySQLCheckpointSaver(db_session.get_bind()))
    result = first.invoke({"value": 7}, config=config)
    assert result["__interrupt__"] and first.get_state(config).next == ("approval",)
    restarted = builder.compile(checkpointer=MySQLCheckpointSaver(db_session.get_bind()))
    resumed = restarted.invoke(Command(resume=True), config=config)
    assert resumed["approved"] is True and restarted.get_state(config).next == ()


def test_checkpoint_blob_contains_compact_state_not_note_body(db_session):
    saver = MySQLCheckpointSaver(db_session.get_bind())
    checkpoint = {"v": 4, "ts": "2026-07-21T00:00:00Z", "id": "cp-1", "channel_values": {"proposal_id": "p", "status": "pending"}, "channel_versions": {}, "versions_seen": {}, "updated_channels": []}
    saver.put({"configurable": {"thread_id": "p", "checkpoint_ns": ""}}, checkpoint, {}, {})
    row = db_session.get(OfficialCheckpoint, ("p", "", "cp-1"))
    assert b"Full private note body" not in row.checkpoint_blob


class StaticFetchService:
    def __init__(self, snapshot):
        self.snapshot = snapshot
    def fetch_source(self, db, source_id):
        del db
        return {"run_id": "fetch-run", "changed": False, "snapshot_id": self.snapshot.id, "content_hash": self.snapshot.content_hash, "http_status": 304, "duration": 1}


def test_official_audit_maps_unexpected_builder_failure_to_stable_error(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)

    class FailingBuilder:
        def build(self, *_args, **_kwargs):
            raise RuntimeError("synthetic internal detail")

    audit = OfficialAuditService(
        stage6_settings,
        fetch_service=StaticFetchService(snapshot),
        proposal_builder=FailingBuilder(),
    )
    with pytest.raises(OfficialSourceError) as caught:
        audit.audit(db_session, source.id)
    assert caught.value.code == "official_proposal_invalid" and caught.value.status_code == 502
    run = db_session.scalar(
        select(AgentRun).where(AgentRun.run_type == "official_audit").order_by(AgentRun.created_at.desc())
    )
    assert run.status == "failed" and run.error_code == "official_proposal_invalid"


def test_official_graph_interrupt_and_restart_resume_apply(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    builder = ProposalBuilder(stage6_settings, llm_client=MockLLM(update_plan))
    audit = OfficialAuditService(
        stage6_settings,
        fetch_service=StaticFetchService(snapshot),
        proposal_builder=builder,
        proposal_service=UpdateProposalService(stage6_settings, index_service=FakeIndexService()),
    )
    pending = audit.audit(db_session, source.id)
    proposal = db_session.get(UpdateProposal, pending["proposal_id"])
    assert pending["status"] == "pending_approval" and audit.pending_state(db_session, proposal.id)["next"] == ["interrupt_for_approval"]
    ApprovalService(stage6_settings).approve(db_session, proposal.id, payload_hash=proposal.payload_hash, confirm_all=True, confirmation_text=f"APPLY {proposal.id[:8]}")
    restarted = OfficialAuditService(
        stage6_settings,
        fetch_service=StaticFetchService(snapshot),
        proposal_builder=ProposalBuilder(stage6_settings, llm_client=MockLLM(update_plan)),
        proposal_service=UpdateProposalService(stage6_settings, index_service=FakeIndexService()),
    )
    result = restarted.resume(db_session, proposal.id, decision="approve")
    assert result["status"] == "applied" and "Version: 2" in path.read_text(encoding="utf-8")
    assert restarted.pending_state(db_session, proposal.id)["next"] == []


@pytest.mark.anyio
async def test_high_risk_internal_tool_returns_approval_required_without_callable(db_session):
    registry = build_tool_registry(Settings(_env_file=None))
    with pytest.raises(ToolExecutionError) as caught:
        await ToolExecutor(registry).execute(
            db_session,
            tool_name="apply_update_proposal",
            raw_input={"proposal_id": str(uuid4()), "payload_hash": "0" * 64},
        )
    assert caught.value.decision == "approval_required" and caught.value.code == "approval_required"


def test_manual_cleanup_dry_run_does_not_delete_pending_or_vault(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    ApprovalService(stage6_settings).create_for_proposal(db_session, proposal.id)
    before = path.read_bytes()
    result = OfficialMaintenanceService(stage6_settings).cleanup(db_session, dry_run=True)
    assert result["dry_run"] is True and proposal.id not in result["proposal_artifact_ids"]
    assert path.read_bytes() == before and db_session.get(UpdateProposal, proposal.id).status == "pending_approval"


def test_official_page_and_source_api_do_not_accept_headers_or_local_paths(client, monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, type=0: [(2, 1, 6, "", ("93.184.216.34", port))])
    page = client.get("/official")
    assert page.status_code == 200 and "全部批准" in page.text and "单项批准" in page.text
    invalid = client.post("/official/sources", json={"name": "x", "url": "file:///secret", "authorization": "token"})
    assert invalid.status_code == 422 and "token" not in invalid.text
    created = client.post("/official/sources", json={"name": "Docs", "url": "https://example.com/docs", "source_type": "auto"})
    assert created.status_code == 201 and created.json()["allowed_host"] == "example.com"
    source_id = created.json()["id"]
    assert client.patch(f"/official/sources/{source_id}", json={"url": "https://other.example"}).status_code == 422
    assert "C:\\" not in page.text and "DEEPSEEK_API_KEY" not in page.text


class SequenceFetcher:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
    def fetch(self, url, *, allowed_host, etag=None, last_modified=None):
        self.calls.append({"url": url, "allowed_host": allowed_host, "etag": etag, "last_modified": last_modified})
        return self.results.pop(0)


def test_fetch_service_deduplicates_equal_hash_and_records_conditional_headers(db_session, stage6_settings):
    source = OfficialSource(name="Official", url="https://example.com", canonical_url="https://example.com/", allowed_host="example.com", source_type="markdown", enabled=True, status="active")
    db_session.add(source); db_session.commit()
    body = b"# Fact\n\nStable official content\n"
    fetcher = SequenceFetcher([
        FetchResult(200, source.url, "text/markdown", body, '"v1"', "Tue, 21 Jul 2026 00:00:00 GMT", 0),
        FetchResult(200, source.url, "text/markdown", body, '"v1"', "Tue, 21 Jul 2026 00:00:00 GMT", 0),
    ])
    service = OfficialFetchService(stage6_settings, fetcher=fetcher)
    first = service.fetch_source(db_session, source.id); second = service.fetch_source(db_session, source.id)
    assert first["changed"] is True and second["changed"] is False
    assert db_session.query(OfficialSourceSnapshot).count() == 1
    assert fetcher.calls[1]["etag"] == '"v1"' and fetcher.calls[1]["last_modified"]


def test_fetch_service_304_does_not_create_snapshot_or_call_normalizer(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    fetcher = SequenceFetcher([FetchResult(304, source.url, None, None, snapshot.etag, snapshot.last_modified, 0)])
    class ExplodingNormalizer:
        def normalize(self, *args, **kwargs):
            raise AssertionError("normalizer must not run for 304")
    result = OfficialFetchService(stage6_settings, fetcher=fetcher, normalizer=ExplodingNormalizer()).fetch_source(db_session, source.id)
    assert result["changed"] is False and result["snapshot_id"] == snapshot.id
    assert db_session.query(OfficialSourceSnapshot).count() == 1


def test_disabled_source_and_global_feature_flag_block_fetch_or_listing(db_session, stage6_settings):
    source = OfficialSource(name="Off", url="https://example.com", canonical_url="https://example.com/", allowed_host="example.com", source_type="auto", enabled=False, status="disabled")
    db_session.add(source); db_session.commit()
    with pytest.raises(OfficialSourceError) as disabled:
        OfficialFetchService(stage6_settings, fetcher=SequenceFetcher([])).fetch_source(db_session, source.id)
    assert disabled.value.code == "official_source_disabled"
    off = stage6_settings.model_copy(update={"official_sources_enabled": False})
    with pytest.raises(OfficialSourceError):
        OfficialSourceService(off).list(db_session)


def test_empty_llm_plan_creates_no_proposal_artifact_or_database_row(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal_id = str(uuid4())
    llm = MockLLM({"summary": "nothing", "items": []})
    result = ProposalBuilder(stage6_settings, llm_client=llm).build(
        db_session, proposal_id=proposal_id, source_id=source.id, snapshot_id=snapshot.id
    )
    assert result is None and llm.calls == 1
    assert db_session.get(UpdateProposal, proposal_id) is None and not (stage6_settings.update_proposal_dir / proposal_id).exists()


def test_proposal_operation_and_change_ratio_limits_are_enforced(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    def oversized(payload):
        result = update_plan(payload)
        result["items"][0]["operations"][0]["replacement_markdown"] = "X" * 5000
        return result
    with pytest.raises(OfficialSourceError) as caught:
        build_proposal(db_session, stage6_settings, source, snapshot, oversized)
    assert caught.value.code == "official_change_ratio_exceeded"
    settings = stage6_settings.model_copy(update={"official_proposal_max_operations_per_note": 1})
    plan = update_plan({"snapshot": {"sections": [{"section_id": snapshot.section_manifest[0]["section_id"]}]}, "notes": [{"note_id": note.id}]})
    plan["items"][0]["operations"] *= 2
    with pytest.raises(OfficialSourceError):
        ProposalBuilder(settings, llm_client=MockLLM(plan)).build(db_session, proposal_id=str(uuid4()), source_id=source.id, snapshot_id=snapshot.id)


def test_duplicate_approval_and_rejection_decisions_are_idempotent(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    service = ApprovalService(stage6_settings); approval = service.create_for_proposal(db_session, proposal.id)
    first = service.reject(db_session, proposal.id, payload_hash=proposal.payload_hash, reason="no")
    second = service.reject(db_session, proposal.id, payload_hash=proposal.payload_hash, reason="ignored")
    assert first[0].status == second[0].status == "rejected" and first[1].id == second[1].id


def test_approval_idempotency_key_fits_mysql_column_and_is_stable(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    service = ApprovalService(stage6_settings)
    first = service.create_for_proposal(db_session, proposal.id)
    second = service.create_for_proposal(db_session, proposal.id)
    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key.startswith("official:") and len(first.idempotency_key) <= 100


def test_approval_execution_lock_rejects_concurrent_resume_and_releases(db_session, stage6_settings):
    service = ApprovalService(stage6_settings)
    engine = db_session.get_bind()
    entered = threading.Event()
    release = threading.Event()
    proposal_id = str(uuid4())

    def hold_lock():
        with Session(bind=engine) as session:
            with service.execution_lock(session, proposal_id):
                entered.set()
                release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(timeout=2)
    with Session(bind=engine) as concurrent_session:
        with pytest.raises(OfficialSourceError) as caught:
            with service.execution_lock(concurrent_session, proposal_id):
                pass
    assert caught.value.code == "official_approval_required"
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    with Session(bind=engine) as retry_session:
        with service.execution_lock(retry_session, proposal_id):
            pass


def test_expired_approval_status_is_persisted(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    proposal = build_proposal(db_session, stage6_settings, source, snapshot)
    service = ApprovalService(stage6_settings); approval = service.create_for_proposal(db_session, proposal.id)
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1); db_session.commit()
    with pytest.raises(OfficialSourceError):
        service.approve(db_session, proposal.id, payload_hash=proposal.payload_hash, confirm_all=True, confirmation_text=f"APPLY {proposal.id[:8]}")
    assert db_session.get(ApprovalRequest, approval.id).status == "expired"


def test_graph_reject_resume_reaches_terminal_without_writing(db_session, stage6_settings):
    source, snapshot, note, path = seed_source_snapshot_note(db_session, stage6_settings)
    before = path.read_bytes()
    audit = OfficialAuditService(
        stage6_settings,
        fetch_service=StaticFetchService(snapshot),
        proposal_builder=ProposalBuilder(stage6_settings, llm_client=MockLLM(update_plan)),
        proposal_service=UpdateProposalService(stage6_settings, index_service=FakeIndexService()),
    )
    pending = audit.audit(db_session, source.id); proposal = db_session.get(UpdateProposal, pending["proposal_id"])
    ApprovalService(stage6_settings).reject(db_session, proposal.id, payload_hash=proposal.payload_hash, reason="manual reject")
    result = OfficialAuditService(
        stage6_settings,
        fetch_service=StaticFetchService(snapshot),
        proposal_builder=ProposalBuilder(stage6_settings, llm_client=MockLLM(update_plan)),
        proposal_service=UpdateProposalService(stage6_settings, index_service=FakeIndexService()),
    ).resume(db_session, proposal.id, decision="reject")
    assert result["status"] == "rejected" and path.read_bytes() == before and db_session.query(NoteVersion).count() == 0


def test_checkpoint_disabled_is_a_stable_error(db_session, stage6_settings):
    disabled = stage6_settings.model_copy(update={"official_checkpoint_enabled": False})
    with pytest.raises(OfficialSourceError) as caught:
        OfficialAuditService(disabled).pending_state(db_session, "proposal")
    assert caught.value.code == "official_checkpoint_unavailable"


def test_snapshot_retention_dry_run_and_apply_keep_latest_five(db_session, stage6_settings):
    source = OfficialSource(name="Official", url="https://example.com", canonical_url="https://example.com/", allowed_host="example.com", source_type="text", enabled=True, status="active")
    db_session.add(source); db_session.flush()
    rows=[]
    for index in range(7):
        snapshot_id=str(uuid4()); text=f"snapshot {index}\n"; storage=SnapshotStore(stage6_settings).save(source.id,snapshot_id,text)
        row=OfficialSourceSnapshot(id=snapshot_id,source_id=source.id,content_hash=hashlib.sha256(text.encode()).hexdigest(),storage_path=storage,normalized_format="text",normalizer_version="official-v1",http_status=200,content_type="text/plain",size_bytes=len(text),section_manifest=build_section_manifest(text),created_at=datetime.now(UTC)+timedelta(seconds=index))
        db_session.add(row); rows.append(row)
    db_session.commit()
    maintenance=OfficialMaintenanceService(stage6_settings)
    preview=maintenance.cleanup(db_session,dry_run=True)
    assert len(preview["snapshot_ids"])==2 and db_session.query(OfficialSourceSnapshot).count()==7
    maintenance.cleanup(db_session,dry_run=False)
    assert db_session.query(OfficialSourceSnapshot).count()==5
