import asyncio
from dataclasses import replace
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from app.core.config import BASE_DIR, Settings
from app.db.models import ToolExecution
from app.mcp.client_manager import MCPClientManager
from app.mcp.client_session import SanitizedStderrCapture
from app.mcp.configuration import MCPConfiguration, MCPServerConfig
from app.mcp.errors import MCPIntegrationError
from app.tools.definitions import CallerType, RiskLevel
from app.tools.executor import ToolExecutionError, ToolExecutor
from app.tools.registry import build_tool_registry


class FakeCapabilities:
    def model_dump(self, **kwargs):
        return {"tools": {"listChanged": False}}


class FakeSession:
    def __init__(self, config, settings, *, tools=None, delay=0, result=None, fail_call=None):
        self.config = config
        self.settings = settings
        self.tools = tools or []
        self.delay = delay
        self.result = result or CallToolResult(structuredContent={"ok": True}, content=[])
        self.fail_call = fail_call
        self.running = False
        self.start_count = 0
        self.call_count = 0
        self.stop_count = 0
        self.stderr = SimpleNamespace(preview="")

    async def start(self):
        self.start_count += 1
        await asyncio.sleep(self.delay)
        self.running = True
        return SimpleNamespace(capabilities=FakeCapabilities())

    async def list_tools(self, *, limit):
        if len(self.tools) > limit:
            raise MCPIntegrationError("mcp_protocol_error", "too many tools")
        return self.tools

    async def call_tool(self, name, arguments, *, timeout_seconds):
        self.call_count += 1
        if self.fail_call:
            raise self.fail_call
        return self.result

    async def stop(self):
        self.stop_count += 1
        self.running = False


def tool(name="echo", schema=None, *, annotation=None, description="test"):
    return Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        annotations=annotation,
    )


def configuration(*, allowed=None, max_tools=20, enabled=True, timeout_seconds=15):
    config = MCPServerConfig(
        name="local",
        enabled=enabled,
        transport="stdio",
        command="python",
        args=["-m", "app.mcp.server"],
        cwd=".",
        timeout_seconds=timeout_seconds,
        max_tools=max_tools,
        allowed_tools=allowed or [],
        auto_connect=False,
    )
    return MCPConfiguration(servers=[config])


def build_manager(*, tools=None, allowed=None, session_options=None, settings=None, max_tools=20, timeout_seconds=15):
    settings = settings or Settings(_env_file=None, app_env="test")
    registry = build_tool_registry(settings)
    created = []

    def factory(config, manager_settings):
        session = FakeSession(config, manager_settings, tools=tools, **(session_options or {}))
        created.append(session)
        return session

    manager = MCPClientManager(
        settings=settings,
        configuration=configuration(allowed=allowed, max_tools=max_tools, timeout_seconds=timeout_seconds),
        registry=registry,
        session_factory=factory,
    )
    return manager, registry, created


@pytest.mark.anyio
async def test_initial_connect_refresh_disconnect_lifecycle():
    manager, registry, created = build_manager(tools=[tool()], allowed=["echo"])
    assert manager.detail("local").status == "disconnected"
    connected = await manager.connect("local")
    assert connected.status == "connected" and connected.tool_count == connected.allowed_tool_count == 1
    assert registry.get("mcp.local.echo") is not None and len(created) == 1
    refreshed = await manager.refresh_tools("local")
    assert refreshed.status == "connected" and len(created) == 1
    disconnected = await manager.disconnect("local")
    assert disconnected.status == "disconnected" and created[0].stop_count == 1
    assert registry.get("mcp.local.echo") is None


@pytest.mark.anyio
async def test_duplicate_and_concurrent_connect_create_one_session():
    manager, _, created = build_manager(tools=[tool()], allowed=["echo"], session_options={"delay": 0.05})
    first, second = await asyncio.gather(manager.connect("local"), manager.connect("local"))
    third = await manager.connect("local")
    assert first.connected and second.connected and third.connected
    assert len(created) == 1 and created[0].start_count == 1
    await manager.close_all()


@pytest.mark.anyio
async def test_allowlist_is_default_deny_and_annotations_cannot_set_risk():
    annotation = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
    manager, registry, _ = build_manager(tools=[tool("allowed", annotation=annotation), tool("blocked")], allowed=["allowed"])
    detail = await manager.connect("local")
    catalog = {item.remote_tool_name: item for item in detail.tools}
    assert catalog["allowed"].allowed is True and catalog["blocked"].allowed is False
    definition = registry.get("mcp.local.allowed")
    assert definition is not None and definition.risk_level is RiskLevel.READ_ONLY
    assert registry.get("mcp.local.blocked") is None
    await manager.close_all()


@pytest.mark.anyio
async def test_invalid_schema_and_tool_limit_never_register_tools():
    manager, registry, _ = build_manager(
        tools=[tool("bad", {"$ref": "https://example.invalid/schema"})], allowed=["bad"]
    )
    detail = await manager.connect("local")
    assert detail.tools[0].allowed is False and registry.get("mcp.local.bad") is None
    await manager.close_all()

    limited, limited_registry, _ = build_manager(tools=[tool("a"), tool("b")], allowed=["a", "b"], max_tools=1)
    with pytest.raises(MCPIntegrationError) as raised:
        await limited.connect("local")
    assert raised.value.code == "mcp_protocol_error" and limited_registry.get("mcp.local.a") is None


@pytest.mark.anyio
async def test_namespaced_tool_executes_through_executor_and_writes_mcp_audit(db_session):
    manager, registry, created = build_manager(tools=[tool()], allowed=["echo"])
    await manager.connect("local")
    output, audit = await ToolExecutor(registry).execute(
        db_session,
        tool_name="mcp.local.echo",
        raw_input={"value": 2},
        caller=CallerType.user,
    )
    assert output["data"] == {"ok": True} and created[0].call_count == 1
    assert audit.tool_source == "mcp" and audit.provider_name == "local" and audit.remote_tool_name == "echo"
    assert audit.protocol_request_id and len(audit.protocol_request_id) <= 100
    await manager.close_all()


@pytest.mark.anyio
@pytest.mark.parametrize("caller", [CallerType.model, CallerType.system])
async def test_mcp_tool_rejects_non_user_callers_without_remote_call(db_session, caller):
    manager, registry, created = build_manager(tools=[tool()], allowed=["echo"])
    await manager.connect("local")
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(registry).execute(
            db_session, tool_name="mcp.local.echo", raw_input={"value": 1}, caller=caller
        )
    assert raised.value.code == "caller_not_allowed" and created[0].call_count == 0
    await manager.close_all()


@pytest.mark.anyio
async def test_input_validation_fails_before_remote_call_and_is_audited(db_session):
    manager, registry, created = build_manager(tools=[tool()], allowed=["echo"])
    await manager.connect("local")
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(registry).execute(db_session, tool_name="mcp.local.echo", raw_input={"value": "bad"})
    assert raised.value.code == "mcp_input_invalid" and created[0].call_count == 0
    audit = db_session.query(ToolExecution).one()
    assert audit.status == "failed" and audit.tool_source == "mcp"
    await manager.close_all()


@pytest.mark.anyio
async def test_output_is_safely_truncated_and_not_sent_to_a_model(db_session):
    settings = Settings(_env_file=None, app_env="test", mcp_max_output_chars=100)
    result = CallToolResult(content=[TextContent(type="text", text="x" * 1000)])
    manager, registry, _ = build_manager(
        tools=[tool()], allowed=["echo"], session_options={"result": result}, settings=settings
    )
    await manager.connect("local")
    output, _ = await ToolExecutor(registry).execute(db_session, tool_name="mcp.local.echo", raw_input={"value": 1})
    assert output["truncated"] is True and output["error_code"] == "mcp_output_too_large"
    assert "model" not in output
    await manager.close_all()


@pytest.mark.anyio
async def test_protocol_failure_is_stable_and_audited(db_session):
    manager, registry, _ = build_manager(
        tools=[tool()],
        allowed=["echo"],
        session_options={"fail_call": MCPIntegrationError("mcp_protocol_error", "protocol failed")},
    )
    await manager.connect("local")
    with pytest.raises(ToolExecutionError) as raised:
        await ToolExecutor(registry).execute(db_session, tool_name="mcp.local.echo", raw_input={"value": 1})
    assert raised.value.code == "mcp_protocol_error"
    audit = db_session.query(ToolExecution).one()
    assert audit.status == "failed" and audit.error_code == "mcp_protocol_error"
    await manager.close_all()


@pytest.mark.anyio
async def test_disconnected_and_unknown_servers_are_rejected():
    manager, _, _ = build_manager(tools=[tool()], allowed=["echo"])
    with pytest.raises(MCPIntegrationError) as disconnected:
        await manager.call_tool("local", "echo", {"value": 1}, timeout_seconds=1)
    assert disconnected.value.code == "mcp_server_not_connected"
    with pytest.raises(MCPIntegrationError) as unknown:
        await manager.connect("unknown")
    assert unknown.value.code == "mcp_server_not_configured"


def test_policy_rejects_mcp_low_and_high_risk_locally():
    manager, registry, _ = build_manager(tools=[tool()], allowed=["echo"])
    # A hostile or buggy local adapter cannot make MCP executable at another risk level.
    base = SimpleNamespace(
        enabled=True,
        tool_source="mcp",
        allowed_callers=frozenset({CallerType.user}),
    )
    from app.tools.policy import ToolPolicy

    for risk in (RiskLevel.LOW_RISK, RiskLevel.HIGH_RISK):
        definition = SimpleNamespace(**base.__dict__, risk_level=risk)
        assert ToolPolicy().evaluate(definition, CallerType.user).decision == "denied"


@pytest.mark.anyio
async def test_connection_timeout_sets_error_and_manual_retry_is_possible():
    settings = Settings(_env_file=None, app_env="test", mcp_default_connect_timeout_seconds=1)
    manager, _, created = build_manager(
        tools=[tool()], allowed=["echo"], session_options={"delay": 2}, settings=settings, timeout_seconds=1
    )
    with pytest.raises(MCPIntegrationError) as raised:
        await manager.connect("local")
    assert raised.value.code == "mcp_timeout" and manager.detail("local").status == "error"

    created.clear()
    manager._session_factory = lambda config, manager_settings: FakeSession(
        config, manager_settings, tools=[tool()]
    )
    retried = await manager.connect("local")
    assert retried.status == "connected"
    await manager.close_all()


@pytest.mark.anyio
async def test_disabled_server_never_starts_a_session():
    settings = Settings(_env_file=None, app_env="test")
    created = []
    manager = MCPClientManager(
        settings=settings,
        configuration=configuration(enabled=False),
        registry=build_tool_registry(settings),
        session_factory=lambda config, manager_settings: created.append(1),
    )
    with pytest.raises(MCPIntegrationError) as raised:
        await manager.connect("local")
    assert raised.value.code == "mcp_server_disabled" and created == []


@pytest.mark.anyio
async def test_server_exit_is_reported_as_not_connected():
    manager, _, created = build_manager(tools=[tool()], allowed=["echo"])
    await manager.connect("local")
    created[0].running = False
    with pytest.raises(MCPIntegrationError) as raised:
        await manager.call_tool("local", "echo", {"value": 1}, timeout_seconds=1)
    assert raised.value.code == "mcp_server_not_connected"
    await manager.close_all()


def test_server_summaries_never_expose_command_url_or_token(monkeypatch):
    monkeypatch.setenv("REMOTE_MCP_TOKEN", "raw-secret")
    settings = Settings(_env_file=None, app_env="test")
    remote = MCPServerConfig(
        name="remote",
        enabled=False,
        transport="streamable_http",
        url="https://example.invalid/mcp",
        allowed_hosts=["example.invalid"],
        auth_env_var="REMOTE_MCP_TOKEN",
        timeout_seconds=15,
        max_tools=5,
        allowed_tools=[],
        auto_connect=False,
    )
    manager = MCPClientManager(
        settings=settings,
        configuration=MCPConfiguration(servers=[remote]),
        registry=build_tool_registry(settings),
    )
    rendered = manager.list_response().model_dump_json()
    assert "raw-secret" not in rendered and "example.invalid" not in rendered and "REMOTE_MCP_TOKEN" not in rendered


def test_stderr_capture_is_project_local_redacted_and_truncated():
    import os

    directory = BASE_DIR / "runtime" / "temp" / f"stderr-test-{uuid4()}"
    try:
        capture = SanitizedStderrCapture(80, directory)
        os.write(capture.fileno(), ("token=raw-secret C:/Users/example/Vault/Secret.md " + "x" * 500).encode())
        preview = capture.preview
        capture.close()
        assert len(preview) <= 80 and "raw-secret" not in preview and "C:/Users" not in preview
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.anyio
async def test_internal_tool_audit_source_remains_internal(db_session):
    registry = build_tool_registry(Settings(_env_file=None, app_env="test"))
    _, audit = await ToolExecutor(registry).execute(
        db_session, tool_name="get_user_preferences", raw_input={}
    )
    assert audit.tool_source == "internal" and audit.provider_name is None and audit.remote_tool_name is None
