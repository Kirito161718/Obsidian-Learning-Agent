from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from app.core.config import BASE_DIR, Settings
from app.mcp.configuration import MCPServerConfig, load_mcp_configuration
from app.mcp.errors import MCPIntegrationError
from app.mcp.security import normalize_tool_result, safe_description, validate_and_copy_schema, validate_tool_input
from mcp.types import CallToolResult, TextContent


@pytest.fixture()
def mcp_temp_dir():
    path = BASE_DIR / "runtime" / "temp" / f"mcp-test-{uuid4()}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_config(path: Path, body: str) -> Settings:
    config_path = path / "servers.yaml"
    config_path.write_text(body, encoding="utf-8")
    return Settings(_env_file=None, app_env="test", mcp_config_path=config_path)


def valid_stdio(name: str = "local") -> str:
    return f"""servers:
  - name: {name}
    enabled: true
    transport: stdio
    command: python
    args: [-m, app.mcp.server]
    cwd: .
    timeout_seconds: 15
    max_tools: 20
    auto_connect: false
"""


def test_mcp_disabled_does_not_require_configuration():
    settings = Settings(_env_file=None, mcp_enabled=False, mcp_config_path="config/does-not-exist.yaml")
    assert settings.mcp_enabled is False


def test_missing_and_invalid_yaml_are_stable_errors(mcp_temp_dir):
    missing = Settings(_env_file=None, mcp_config_path=mcp_temp_dir / "missing.yaml")
    with pytest.raises(MCPIntegrationError) as raised:
        load_mcp_configuration(missing)
    assert raised.value.code == "mcp_server_not_configured"

    invalid = write_config(mcp_temp_dir, "servers: [")
    with pytest.raises(MCPIntegrationError) as raised:
        load_mcp_configuration(invalid)
    assert raised.value.code == "mcp_server_not_configured"


def test_allowed_tools_defaults_empty(mcp_temp_dir):
    config = load_mcp_configuration(write_config(mcp_temp_dir, valid_stdio()))
    assert config.servers[0].allowed_tools == []


@pytest.mark.parametrize(
    "body",
    [
        valid_stdio("duplicate") + valid_stdio("duplicate").replace("servers:\n", "", 1),
        valid_stdio().replace("stdio", "unknown"),
        valid_stdio().replace("    command: python\n", ""),
        valid_stdio().replace("    cwd: .", "    cwd: ../../outside"),
        valid_stdio().replace("    auto_connect: false", "    auto_connect: true"),
    ],
)
def test_invalid_server_configurations_are_rejected(mcp_temp_dir, body):
    with pytest.raises(MCPIntegrationError) as raised:
        load_mcp_configuration(write_config(mcp_temp_dir, body))
    assert raised.value.code == "mcp_server_not_configured"


def test_duplicate_names_are_rejected(mcp_temp_dir):
    body = """servers:
  - {name: duplicate, enabled: true, transport: stdio, command: python, cwd: ., timeout_seconds: 15, max_tools: 20, auto_connect: false}
  - {name: duplicate, enabled: true, transport: stdio, command: python, cwd: ., timeout_seconds: 15, max_tools: 20, auto_connect: false}
"""
    with pytest.raises(MCPIntegrationError):
        load_mcp_configuration(write_config(mcp_temp_dir, body))


@pytest.mark.parametrize(
    ("url", "hosts", "valid"),
    [
        ("https://example.invalid/mcp", ["example.invalid"], True),
        ("http://localhost:8000/mcp", ["localhost"], True),
        ("http://example.invalid/mcp", ["example.invalid"], False),
        ("https://example.invalid/mcp", ["other.invalid"], False),
        ("https://user:secret@example.invalid/mcp", ["example.invalid"], False),
    ],
)
def test_streamable_http_url_policy(url, hosts, valid):
    values = dict(
        name="remote",
        enabled=False,
        transport="streamable_http",
        url=url,
        allowed_hosts=hosts,
        auth_env_var="REMOTE_MCP_TOKEN",
        timeout_seconds=15,
        max_tools=20,
        auto_connect=False,
    )
    if valid:
        assert MCPServerConfig.model_validate(values).url == url
    else:
        with pytest.raises(ValueError):
            MCPServerConfig.model_validate(values)


@pytest.mark.parametrize(
    "body",
    [
        valid_stdio().replace("args: [-m, app.mcp.server]", "args: [--token=raw-secret]"),
        valid_stdio().replace("    cwd: .", "    cwd: .\n    token: raw-secret"),
    ],
)
def test_inline_secrets_are_rejected(mcp_temp_dir, body):
    with pytest.raises(MCPIntegrationError):
        load_mcp_configuration(write_config(mcp_temp_dir, body))


def test_local_config_is_git_ignored():
    text = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "config/mcp_servers.local.yaml" in text


def test_schema_hash_is_stable_and_changes_with_schema():
    first, first_hash = validate_and_copy_schema({"type": "object", "properties": {"q": {"type": "string"}}}, max_chars=5000)
    second, second_hash = validate_and_copy_schema({"properties": {"q": {"type": "string"}}, "type": "object"}, max_chars=5000)
    _, changed_hash = validate_and_copy_schema({"type": "object", "properties": {"q": {"type": "integer"}}}, max_chars=5000)
    assert first == second and first_hash == second_hash and changed_hash != first_hash


def test_recursive_external_and_huge_schemas_are_rejected():
    recursive = {"$defs": {"node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/node"}}}}, "$ref": "#/$defs/node"}
    external = {"$ref": "https://example.invalid/schema"}
    for schema in (recursive, external, {"description": "x" * 1000, "type": "object"}):
        with pytest.raises(MCPIntegrationError) as raised:
            validate_and_copy_schema(schema, max_chars=100)
        assert raised.value.code == "mcp_schema_invalid"


def test_schema_validation_rejects_invalid_input_before_call():
    schema = {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1}}, "required": ["limit"]}
    with pytest.raises(MCPIntegrationError) as raised:
        validate_tool_input({"limit": 0}, schema)
    assert raised.value.code == "mcp_input_invalid"


def test_description_and_output_are_redacted_and_bounded():
    assert len(safe_description("token=secret " + "x" * 1000, max_chars=50)) <= 50
    result = CallToolResult(
        content=[TextContent(type="text", text="C:/Users/example/Vault/Secret.md token=raw " + "x" * 1000)]
    )
    normalized = normalize_tool_result(result, max_chars=100)
    rendered = str(normalized)
    assert normalized["truncated"] is True
    assert "C:/Users" not in rendered and "token=raw" not in rendered
