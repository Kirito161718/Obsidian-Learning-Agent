from types import SimpleNamespace
from datetime import UTC, datetime
import hashlib
import shutil

import pytest
from pydantic import BaseModel

from app.core.config import BASE_DIR, Settings
from app.db.models import (
    AgentRun,
    OfficialSource,
    OfficialSourceSnapshot,
)
from app.mcp.client_manager import MCPClientManager
from app.mcp.configuration import MCPConfiguration, MCPServerConfig
from app.official_sources.errors import OfficialSourceError
from app.official_sources.fetcher import FetchResult
from app.observability import reset_observability, set_observability_for_tests
from app.observability.configuration import ObservabilityConfiguration
from app.observability.manager import ObservabilityManager
from app.observability.testing import FakeCollector
from app.schemas.rag import AskRequest, RetrieveRequest
from app.services.llm_service import DeepSeekClient
from app.services.official_audit_service import OfficialAuditService
from app.services.official_source_service import OfficialFetchService
from app.services.rag_service import RagService
from app.tools.definitions import CallerType, RiskLevel, ToolDefinition
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def fake_observability():
    settings = Settings(
        _env_file=None,
        app_env="development",
        langsmith_tracing=True,
        langsmith_observability_enabled=True,
        langsmith_api_key="test-key",
    )
    config = ObservabilityConfiguration.from_settings(
        settings,
        allow_test_environment=True,
    )
    collector = FakeCollector()
    manager = ObservabilityManager(config, collector=collector)
    set_observability_for_tests(manager)
    yield manager, collector
    reset_observability()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def raw_hit(text="Query rewrite improves retrieval", *, chunk_id="chunk-1"):
    return {
        "score": 0.91,
        "payload": {
            "chunk_id": chunk_id,
            "source_id": "note-1",
            "source_type": "obsidian_note",
            "source_name": "RAG",
            "source_path": "Knowledge/RAG.md",
            "section_title": "Rewrite",
            "chunk_index": 0,
            "text": text,
        },
    }


class FakeEmbedding:
    vector_size = 3

    def embed_query(self, query):
        return [0.1, 0.2, 0.3]


class FakeVector:
    def search(self, **kwargs):
        return [raw_hit()]


class FakeRerank:
    def rerank(self, query, chunks, top_n):
        return chunks[:top_n]


class FakeLlm:
    model = "fake-deepseek"

    def rewrite_query(self, question, session_memory=None):
        return "rewritten query"

    def generate_answer(self, question, context, **kwargs):
        return "safe answer [来源1]"


def rag_service():
    return RagService(FakeEmbedding(), FakeVector(), FakeRerank(), FakeLlm())


def test_rag_ask_has_one_root_and_nested_stable_spans(
    db_session,
    fake_observability,
):
    _, collector = fake_observability
    question = "private user question"
    response = rag_service().ask(AskRequest(question=question), db_session)
    roots = [span for span in collector.spans if span.parent_run_id is None]
    assert [span.name for span in roots] == ["ola.rag.ask"]
    names = {span.name for span in collector.spans}
    assert {
        "ola.rag.load_context",
        "ola.memory.session.load",
        "ola.rag.retrieve.original",
        "ola.rag.rewrite.decide",
        "ola.rag.rerank",
        "ola.rag.context.build",
        "ola.rag.citations.validate",
        "ola.rag.persist",
    }.issubset(names)
    assert all(span.trace_id == roots[0].trace_id for span in collector.spans)
    assert question not in str([span.metadata for span in collector.spans])
    run = db_session.get(AgentRun, response.run_id)
    assert run.external_trace_id == roots[0].trace_id
    assert run.observability_status == "submitted"


def test_rag_retrieve_has_one_root_and_does_not_change_result(
    db_session,
    fake_observability,
):
    _, collector = fake_observability
    response = rag_service().retrieve(RetrieveRequest(question="retrieve secret"), db_session)
    roots = [span for span in collector.spans if span.parent_run_id is None]
    assert [span.name for span in roots] == ["ola.rag.retrieve"]
    assert response.retrieved_count == 1
    assert response.chunks[0].chunk_id == "chunk-1"


def test_observability_failure_does_not_change_rag_answer(db_session):
    settings = Settings(
        _env_file=None,
        app_env="development",
        langsmith_tracing=True,
        langsmith_observability_enabled=True,
        langsmith_api_key="test-key",
    )
    config = ObservabilityConfiguration.from_settings(settings, allow_test_environment=True)
    set_observability_for_tests(
        ObservabilityManager(config, collector=FakeCollector(fail_open=True))
    )
    response = rag_service().ask(AskRequest(question="safe topic"), db_session)
    assert response.answer == "safe answer [来源1]"
    run = db_session.get(AgentRun, response.run_id)
    assert run.status == "succeeded"
    assert run.observability_status == "failed"


def test_deepseek_llm_span_records_real_usage_without_messages(fake_observability):
    _, collector = fake_observability
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="answer body"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        ),
    )
    client = object.__new__(DeepSeekClient)
    client.model = "deepseek-test"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )
    )
    answer = client.generate_answer("private question", "private chunk")
    span = collector.spans[-1]
    assert answer == "answer body"
    assert span.name == "ola.llm.deepseek.answer" and span.run_type == "llm"
    assert span.metadata["input_token_count"] == 11
    assert span.metadata["output_token_count"] == 7
    serialized = str(span.metadata)
    assert "private question" not in serialized
    assert "private chunk" not in serialized
    assert "answer body" not in serialized


def test_deepseek_usage_absence_is_not_fabricated(fake_observability):
    _, collector = fake_observability
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="answer"),
                finish_reason=None,
            )
        ],
        usage=None,
    )
    client = object.__new__(DeepSeekClient)
    client.model = "deepseek-test"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)
        )
    )
    client.generate_answer("q", "context")
    metadata = collector.spans[-1].metadata
    assert metadata["token_usage_status"] == "unavailable"
    assert "input_token_count" not in metadata


class ValueInput(BaseModel):
    value: int


class ValueOutput(BaseModel):
    value: int


@pytest.mark.anyio
async def test_tool_trace_links_local_audit_without_payload(
    db_session,
    fake_observability,
):
    _, collector = fake_observability
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="safe_value",
            description="test",
            risk_level=RiskLevel.READ_ONLY,
            input_schema=ValueInput,
            output_schema=ValueOutput,
            callable=lambda payload, context: {"value": payload.value},
            enabled=True,
            idempotent=True,
            timeout_seconds=1,
            allowed_callers=frozenset({CallerType.user}),
        )
    )
    result, record = await ToolExecutor(registry).execute(
        db_session,
        tool_name="safe_value",
        raw_input={"value": 987654},
    )
    span = collector.spans[-1]
    assert result == {"value": 987654}
    assert span.name == "ola.tool.execute"
    assert span.metadata["tool_execution_id"] == record.id
    assert "987654" not in str(span.metadata)


def test_observability_status_api_and_page_are_safe(client, fake_observability):
    status = client.get("/agent/observability/status")
    assert status.status_code == 200
    body = status.json()
    assert body["provider"] == "langsmith"
    serialized = status.text
    assert "test-key" not in serialized
    assert "workspace_id" not in serialized
    page = client.get("/agent")
    assert page.status_code == 200
    assert "可观测性" in page.text
    assert "LANGSMITH_API_KEY" not in page.text


class FakeMcpSession:
    def __init__(self, config, settings):
        del settings
        self.config = config
        self.running = False

    async def start(self):
        self.running = True
        return SimpleNamespace(capabilities=SimpleNamespace(model_dump=lambda **kwargs: {}))

    async def list_tools(self, *, limit):
        del limit
        return [
            SimpleNamespace(
                name="lookup",
                description="safe lookup",
                inputSchema={"type": "object"},
            )
        ]

    async def call_tool(self, name, arguments, *, timeout_seconds):
        del name, timeout_seconds
        return {"value": arguments["value"]}, "protocol-request-id"

    async def stop(self):
        self.running = False


@pytest.mark.anyio
async def test_mcp_connect_discover_call_disconnect_have_safe_spans(
    fake_observability,
):
    _, collector = fake_observability
    settings = Settings(_env_file=None, app_env="test", mcp_enabled=True)
    server = MCPServerConfig(
        name="local",
        enabled=True,
        transport="stdio",
        command="python",
        allowed_tools=["lookup"],
        timeout_seconds=2,
        max_tools=5,
        project_root=BASE_DIR,
    )
    manager = MCPClientManager(
        settings=settings,
        configuration=MCPConfiguration(servers=[server]),
        registry=ToolRegistry(),
        session_factory=FakeMcpSession,
    )
    await manager.connect("local")
    result, _ = await manager.call_tool(
        "local",
        "lookup",
        {"value": "private-mcp-payload"},
        timeout_seconds=2,
    )
    await manager.disconnect("local")
    assert result == {"value": "private-mcp-payload"}
    assert [span.name for span in collector.spans] == [
        "ola.mcp.connect",
        "ola.mcp.discover",
        "ola.mcp.call",
        "ola.mcp.disconnect",
    ]
    assert collector.spans[1].parent_run_id == collector.spans[0].id
    assert "private-mcp-payload" not in str([span.metadata for span in collector.spans])


class StaticFetcher:
    def __init__(self, body: bytes):
        self.body = body

    def fetch(self, url, *, allowed_host, etag=None, last_modified=None):
        del allowed_host, etag, last_modified
        return FetchResult(
            200,
            url,
            "text/markdown",
            self.body,
            None,
            None,
            0,
        )


def test_official_fetch_trace_contains_hash_stats_not_snapshot_body(
    db_session,
    fake_observability,
    tmp_path,
    monkeypatch,
):
    _, collector = fake_observability
    runtime_root = BASE_DIR / "runtime" / "pytest-stage7a" / tmp_path.name
    monkeypatch.setattr(
        Settings,
        "official_snapshot_dir",
        property(lambda self: runtime_root / "snapshots"),
    )
    settings = Settings(_env_file=None, app_env="test")
    source = OfficialSource(
        name="Official",
        url="https://example.com/docs",
        canonical_url="https://example.com/docs",
        allowed_host="example.com",
        source_type="markdown",
        enabled=True,
        status="active",
    )
    db_session.add(source)
    db_session.commit()
    body = b"# Fact\n\nPrivate snapshot body must not be traced.\n"
    try:
        result = OfficialFetchService(
            settings,
            fetcher=StaticFetcher(body),
        ).fetch_source(db_session, source.id)
        span = collector.spans[-1]
        assert span.name == "ola.official.fetch"
        assert span.metadata["official_source_id"] == source.id
        assert span.metadata["snapshot_id"] == result["snapshot_id"]
        assert "Private snapshot body" not in str(span.metadata)
        run = db_session.get(AgentRun, result["run_id"])
        assert run.external_trace_id == span.trace_id
        assert run.observability_status == "submitted"
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)


class StaticAuditFetch:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch_source(self, db, source_id):
        del db
        return {
            "run_id": "fetch-run",
            "changed": True,
            "snapshot_id": self.snapshot.id,
            "content_hash": self.snapshot.content_hash,
            "http_status": 200,
            "duration": 1,
        }


class FailingProposalBuilder:
    def build(self, *args, **kwargs):
        raise RuntimeError("private proposal body")


def test_official_audit_failure_keeps_business_error_separate_and_trace_safe(
    db_session,
    fake_observability,
):
    _, collector = fake_observability
    source = OfficialSource(
        name="Official",
        url="https://example.com/docs",
        canonical_url="https://example.com/docs",
        allowed_host="example.com",
        source_type="markdown",
        enabled=True,
        status="active",
    )
    db_session.add(source)
    db_session.flush()
    snapshot = OfficialSourceSnapshot(
        source_id=source.id,
        content_hash=hashlib.sha256(b"body").hexdigest(),
        storage_path="safe-relative-path",
        normalized_format="markdown",
        normalizer_version="official-v1",
        http_status=200,
        content_type="text/markdown",
        size_bytes=4,
        section_manifest=[],
        created_at=datetime.now(UTC),
    )
    db_session.add(snapshot)
    db_session.commit()
    service = OfficialAuditService(
        Settings(_env_file=None, app_env="test"),
        fetch_service=StaticAuditFetch(snapshot),
        proposal_builder=FailingProposalBuilder(),
    )
    with pytest.raises(OfficialSourceError) as caught:
        service.audit(db_session, source.id)
    assert caught.value.code == "official_proposal_invalid"
    roots = [span for span in collector.spans if span.parent_run_id is None]
    assert [span.name for span in roots] == ["ola.official.audit"]
    assert "ola.official.proposal.build" in {span.name for span in collector.spans}
    assert "private proposal body" not in str([span.metadata for span in collector.spans])
    run = db_session.query(AgentRun).filter(AgentRun.run_type == "official_audit").one()
    assert run.status == "failed"
