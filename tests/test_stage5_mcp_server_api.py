import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, Tool

from app.core.config import BASE_DIR, Settings
from app.mcp import server as mcp_server
from app.mcp.client_manager import MCPClientManager
from app.mcp.configuration import MCPConfiguration, MCPServerConfig
from app.tools.registry import build_tool_registry, get_tool_registry


class SessionProxy:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        return None


@pytest.mark.anyio
async def test_project_server_registers_only_six_tools_and_survives_tool_error(monkeypatch, db_session):
    monkeypatch.setattr(mcp_server, "SessionLocal", lambda: SessionProxy(db_session))
    tools = await mcp_server.handle_list_tools()
    assert {item.name for item in tools} == {
        "list_learning_goals",
        "list_learning_status",
        "search_obsidian_notes",
        "get_session_messages",
        "get_user_preferences",
        "retrieve_knowledge",
    }
    assert len(tools) == 6 and all(item.inputSchema["type"] == "object" for item in tools)

    failed = await mcp_server.handle_call_tool("search_obsidian_notes", {"path_prefix": "C:/Vault"})
    assert isinstance(failed, CallToolResult) and failed.isError is True
    assert "Traceback" not in failed.content[0].text
    succeeded = await mcp_server.handle_call_tool("get_user_preferences", {})
    assert succeeded["id"] == "default"
    assert len(await mcp_server.handle_list_tools()) == 6


@pytest.mark.anyio
async def test_real_stdio_handshake_tools_only_and_disconnects_cleanly():
    executable = Path(sys.executable).resolve()
    if not executable.is_relative_to(BASE_DIR.resolve()):
        pytest.skip("The project-local virtual environment is required for the stdio acceptance test")
    config = MCPServerConfig(
        name="project_server",
        enabled=True,
        transport="stdio",
        command=str(executable.relative_to(BASE_DIR)).replace("\\", "/"),
        args=["-m", "app.mcp.server"],
        cwd=".",
        timeout_seconds=30,
        max_tools=20,
        allowed_tools=[
            "list_learning_goals",
            "list_learning_status",
            "search_obsidian_notes",
            "get_session_messages",
            "get_user_preferences",
            "retrieve_knowledge",
        ],
        environment_allowlist=["APP_ENV"],
        auto_connect=False,
    )
    settings = Settings(_env_file=None, app_env="test", mcp_default_connect_timeout_seconds=30)
    manager = MCPClientManager(
        settings=settings,
        configuration=MCPConfiguration(servers=[config]),
        registry=build_tool_registry(settings),
    )
    try:
        detail = await manager.connect("project_server")
        assert detail.status == "connected" and detail.tool_count == detail.allowed_tool_count == 6
        assert "tools" in detail.capabilities
        assert "resources" not in detail.capabilities and "prompts" not in detail.capabilities
        first_session = manager._states["project_server"].session
        assert first_session is not None and first_session.running
        await manager.refresh_tools("project_server")
        assert manager._states["project_server"].session is first_session
    finally:
        await manager.close_all()
    assert first_session.running is False


class APIFakeSession:
    def __init__(self, config, settings):
        self.running = False
        self.calls = 0
        self.tools = [Tool(name="echo", description="echo", inputSchema={"type": "object"})]

    async def start(self):
        self.running = True
        return SimpleNamespace(capabilities=SimpleNamespace(model_dump=lambda **_: {"tools": {}}))

    async def list_tools(self, *, limit):
        return self.tools

    async def call_tool(self, name, arguments, *, timeout_seconds):
        self.calls += 1
        return CallToolResult(structuredContent={"echo": arguments}, content=[])

    async def stop(self):
        self.running = False


def api_manager(*, enabled=True):
    settings = Settings(_env_file=None, app_env="test", mcp_enabled=enabled)
    configuration = MCPConfiguration(
        servers=[
            MCPServerConfig(
                name="api_server",
                enabled=True,
                transport="stdio",
                command="python",
                args=[],
                cwd=".",
                timeout_seconds=15,
                max_tools=5,
                allowed_tools=["echo"],
                auto_connect=False,
            )
        ]
    ) if enabled else None
    return MCPClientManager(
        settings=settings,
        configuration=configuration,
        registry=get_tool_registry(),
        configuration_status="loaded" if enabled else "disabled",
        session_factory=APIFakeSession,
    )


def test_mcp_api_lifecycle_list_and_original_execute_endpoint(client):
    manager = api_manager()
    client.app.state.mcp_manager = manager
    initial = client.get("/agent/mcp/servers")
    assert initial.status_code == 200 and initial.json()["servers"][0]["status"] == "disconnected"

    connected = client.post(
        "/agent/mcp/servers/api_server/connect",
        json={"command": "powershell", "url": "https://attacker.invalid", "allowed_tools": ["write"]},
    )
    assert connected.status_code == 200 and connected.json()["server"]["allowed_tool_count"] == 1
    tools = client.get("/agent/mcp/tools?allowed=true&connected=true")
    assert tools.status_code == 200 and tools.json()["items"][0]["namespaced_name"] == "mcp.api_server.echo"

    executed = client.post("/agent/tools/mcp.api_server.echo/execute", json={"input": {"value": 2}})
    assert executed.status_code == 200
    audit = executed.json()["execution"]
    assert audit["tool_source"] == "mcp" and audit["provider_name"] == "api_server"

    refreshed = client.post("/agent/mcp/servers/api_server/refresh-tools")
    assert refreshed.status_code == 200
    disconnected = client.post("/agent/mcp/servers/api_server/disconnect")
    assert disconnected.status_code == 200 and disconnected.json()["server"]["connected"] is False
    rejected = client.post("/agent/tools/mcp.api_server.echo/execute", json={"input": {}})
    assert rejected.status_code == 404


def test_mcp_api_disabled_unknown_and_page_safety(client):
    client.app.state.mcp_manager = api_manager(enabled=False)
    response = client.get("/agent/mcp/servers")
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert client.post("/agent/mcp/servers/unknown/connect").status_code == 404
    page = client.get("/agent")
    assert page.status_code == 200 and "MCP Servers" in page.text and "MCP Tools" in page.text
    assert "C:\\Users" not in page.text and "EXAMPLE_MCP_TOKEN" not in page.text
