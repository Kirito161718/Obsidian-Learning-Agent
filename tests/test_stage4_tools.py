import asyncio

import pytest
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.db.models import LearningGoal, LearningSession, LearningStatus, ObsidianNote, QAHistory, ToolExecution, UserPreference
from app.schemas.rag import RetrieveResponse, RetrievedChunk
from app.tools.definitions import CallerType, RiskLevel, ToolDefinition
from app.tools.executor import ToolExecutionError, ToolExecutor
from app.tools.registry import ToolRegistry, build_tool_registry


class ValueInput(BaseModel):
    value: int = Field(ge=0)


class ValueOutput(BaseModel):
    value: int


@pytest.fixture
def anyio_backend():
    return "asyncio"


def definition(name, callable_obj, *, risk=RiskLevel.READ_ONLY, enabled=True, timeout=1, output=ValueOutput):
    return ToolDefinition(
        name=name,
        description="test tool",
        risk_level=risk,
        input_schema=ValueInput,
        output_schema=output,
        callable=callable_obj,
        enabled=enabled,
        idempotent=True,
        timeout_seconds=timeout,
        allowed_callers=frozenset({CallerType.user}),
    )


def registry_with(item):
    registry = ToolRegistry()
    registry.register(item)
    return registry


@pytest.mark.anyio
async def test_read_only_tool_executes_and_writes_success_audit(db_session):
    executor = ToolExecutor(registry_with(definition("double", lambda payload, _: {"value": payload.value * 2})))
    output, audit = await executor.execute(db_session, tool_name="double", raw_input={"value": 3})
    assert output == {"value": 6}
    assert audit.risk_level == "READ_ONLY"
    assert audit.decision == "allowed" and audit.status == "succeeded"
    assert len(audit.input_hash) == 64 and audit.duration_ms is not None


@pytest.mark.anyio
async def test_unregistered_tool_is_rejected_and_audited(db_session):
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(ToolRegistry()).execute(db_session, tool_name="unknown_tool", raw_input={})
    assert raised.value.code == "tool_not_registered"
    audit = db_session.query(ToolExecution).one()
    assert audit.status == "denied" and audit.risk_level is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("risk", "enabled", "code", "decision"),
    [
        (RiskLevel.READ_ONLY, False, "tool_disabled", "denied"),
        (RiskLevel.LOW_RISK, True, "low_risk_tools_not_enabled", "denied"),
        (RiskLevel.HIGH_RISK, True, "approval_required", "approval_required"),
    ],
)
async def test_policy_denies_disabled_low_and_high_without_calling(db_session, risk, enabled, code, decision):
    calls = []
    item = definition("guarded", lambda payload, _: calls.append(payload) or {"value": 1}, risk=risk, enabled=enabled)
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(registry_with(item)).execute(db_session, tool_name="guarded", raw_input={"value": 1})
    assert raised.value.code == code and raised.value.decision == decision
    assert calls == []


@pytest.mark.anyio
async def test_caller_cannot_be_forged_and_model_is_denied(db_session):
    item = definition("caller_guard", lambda payload, _: {"value": payload.value})
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(registry_with(item)).execute(
            db_session,
            tool_name="caller_guard",
            raw_input={"value": 1, "caller_type": "user", "risk_level": "READ_ONLY"},
            caller=CallerType.model,
        )
    assert raised.value.code == "caller_not_allowed"
    assert db_session.query(ToolExecution).one().caller_type == "model"


@pytest.mark.anyio
async def test_input_and_output_schema_validation_are_audited(db_session):
    executor = ToolExecutor(registry_with(definition("validate", lambda payload, _: {"bad": 1})))
    with pytest.raises(ToolExecutionError) as invalid_input:
        await executor.execute(db_session, tool_name="validate", raw_input={"value": -1})
    assert invalid_input.value.code == "input_validation_failed"
    with pytest.raises(ToolExecutionError) as invalid_output:
        await executor.execute(db_session, tool_name="validate", raw_input={"value": 1})
    assert invalid_output.value.code == "output_validation_failed"
    assert {row.error_code for row in db_session.query(ToolExecution).all()} == {
        "input_validation_failed",
        "output_validation_failed",
    }


@pytest.mark.anyio
async def test_tool_timeout_and_exception_do_not_return_traceback(db_session):
    async def slow(payload, context):
        await asyncio.sleep(2)
        return {"value": payload.value}

    async def broken(payload, context):
        raise RuntimeError("password=hunter2")

    for name, callable_obj, expected in (("slow", slow, "tool_timeout"), ("broken", broken, "tool_execution_failed")):
        with pytest.raises(ToolExecutionError) as raised:
            await ToolExecutor(registry_with(definition(name, callable_obj, timeout=1))).execute(
                db_session, tool_name=name, raw_input={"value": 1}
            )
        assert raised.value.code == expected
        assert "Traceback" not in str(raised.value) and "hunter2" not in str(raised.value)


@pytest.mark.anyio
async def test_audit_redacts_sensitive_input_output_and_absolute_paths(db_session):
    class SecretInput(BaseModel):
        token: str
        path: str

    class SecretOutput(BaseModel):
        authorization: str
        content: str

    item = ToolDefinition(
        name="redact",
        description="redact",
        risk_level=RiskLevel.READ_ONLY,
        input_schema=SecretInput,
        output_schema=SecretOutput,
        callable=lambda payload, _: {"authorization": "Bearer abc", "content": "C:/Users/example/Vault/Secret.md " + "x" * 2000},
        enabled=True,
        idempotent=True,
        timeout_seconds=2,
        allowed_callers=frozenset({CallerType.user}),
    )
    _, audit = await ToolExecutor(registry_with(item)).execute(
        db_session,
        tool_name="redact",
        raw_input={"token": "raw-secret", "path": "C:/Users/example/Vault/Secret.md"},
    )
    rendered = str(audit.input_summary) + str(audit.output_summary)
    assert "raw-secret" not in rendered and "Bearer abc" not in rendered
    assert "C:/Users" not in rendered and audit.output_summary["truncated"] is True


def seed_internal_data(db):
    goal = LearningGoal(name="Stage 4", goal_type="project", priority=5, status="active", related_topics=["RAG"])
    db.add(goal)
    db.commit()
    status = LearningStatus(
        topic="RAG",
        status="learning",
        mastery_level=2,
        manual_importance=4,
        related_goal_ids=[goal.id],
        project_relevance=True,
    )
    note = ObsidianNote(
        relative_path="Knowledge/RAG.md",
        file_name="RAG.md",
        title="RAG",
        tags=["rag"],
        index_status="indexed",
        chunk_count=2,
    )
    session = LearningSession(title="S", source_types=["obsidian_note"], source_path_prefixes=[])
    db.add_all([status, note, session])
    db.commit()
    db.add(QAHistory(question="q", rewritten_query="q", answer="a", source_chunks=[], session_id=session.id))
    db.commit()
    return goal, status, note, session


@pytest.mark.anyio
async def test_initial_internal_tools_are_unique_read_only_enabled_and_bounded(db_session):
    goal, status, note, session = seed_internal_data(db_session)
    registry = build_tool_registry(Settings(_env_file=None))
    names = [item.name for item in registry.list()]
    assert len(names) == len(set(names)) == 6
    assert all(item.risk_level is RiskLevel.READ_ONLY and item.idempotent for item in registry.list())
    executor = ToolExecutor(registry)
    goals, _ = await executor.execute(db_session, tool_name="list_learning_goals", raw_input={"limit": 1})
    statuses, _ = await executor.execute(db_session, tool_name="list_learning_status", raw_input={"limit": 1})
    notes, _ = await executor.execute(db_session, tool_name="search_obsidian_notes", raw_input={"query": "RAG", "limit": 1})
    messages, _ = await executor.execute(db_session, tool_name="get_session_messages", raw_input={"session_id": session.id, "limit": 1})
    prefs, _ = await executor.execute(db_session, tool_name="get_user_preferences", raw_input={})
    assert goals["items"][0]["id"] == goal.id
    assert statuses["items"][0]["importance_score"] > 0
    assert notes["items"][0]["relative_path"] == "Knowledge/RAG.md" and ":/" not in notes["items"][0]["relative_path"]
    assert messages["items"][0]["question"] == "q" and "source_chunks" not in messages["items"][0]
    assert prefs["id"] == "default" and db_session.query(UserPreference).count() == 0


class FakeRetrieveService:
    def retrieve(self, request, db):
        return RetrieveResponse(
            question=request.question,
            rewritten_query=request.question,
            rewrite_applied=False,
            top_k=request.top_k,
            retrieved_count=1,
            chunks=[
                RetrievedChunk(
                    chunk_id="c",
                    filename="RAG.md",
                    source_type="obsidian_note",
                    source_id="n",
                    source_name="RAG",
                    source_path="Knowledge/RAG.md",
                    section_title="Intro",
                    chunk_index=0,
                    score=0.9,
                    text="knowledge",
                )
            ],
            run_id="run-id",
        )


@pytest.mark.anyio
async def test_retrieve_knowledge_reuses_service_and_never_writes_qa(db_session):
    registry = build_tool_registry(Settings(_env_file=None))
    before = db_session.query(QAHistory).count()
    output, _ = await ToolExecutor(registry).execute(
        db_session,
        tool_name="retrieve_knowledge",
        raw_input={"question": "RAG", "source_types": ["obsidian_note"], "top_k": 1},
        services={"rag_service": FakeRetrieveService()},
    )
    assert output["retrieved_count"] == 1 and output["chunks"][0]["source_path"] == "Knowledge/RAG.md"
    assert db_session.query(QAHistory).count() == before


def test_tool_registry_api_never_returns_callable_path(client):
    response = client.get("/agent/tools")
    assert response.status_code == 200 and len(response.json()) == 6
    assert "callable" not in response.text and "app.tools.internal" not in response.text
    assert client.get("/agent/tools/unknown_tool").status_code == 404


def test_tool_execute_api_fixes_caller_to_user_and_unknown_is_404(client, db_session):
    response = client.post(
        "/agent/tools/get_user_preferences/execute",
        json={"input": {"caller_type": "model", "risk_level": "HIGH_RISK"}},
    )
    assert response.status_code == 200
    execution = response.json()["execution"]
    assert execution["caller_type"] == "user" and execution["risk_level"] == "READ_ONLY"
    unknown = client.post("/agent/tools/unknown_tool/execute", json={"input": {}})
    assert unknown.status_code == 404
    assert db_session.query(ToolExecution).count() == 2
