from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import BASE_DIR, Settings
from app.mcp.errors import MCPIntegrationError

SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BARE_COMMAND = re.compile(r"^[A-Za-z0-9_.-]+$")
SENSITIVE_ARGUMENT = re.compile(
    r"(?i)(?:^|[-_/])(password|passwd|api[-_]?key|token|secret|authorization)(?:$|[=:])"
)


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    enabled: bool
    transport: Literal["stdio", "streamable_http"]
    allowed_tools: list[str] = Field(default_factory=list, max_length=500)
    timeout_seconds: int = Field(ge=1, le=600)
    max_tools: int = Field(ge=1, le=500)
    auto_connect: bool = False

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    environment_allowlist: list[str] = Field(default_factory=list)

    url: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    auth_env_var: str | None = None

    project_root: Path = Field(default=BASE_DIR, exclude=True)

    @model_validator(mode="after")
    def validate_server(self) -> Self:
        if not SERVER_NAME.fullmatch(self.name):
            raise ValueError("Server name contains unsupported characters")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools contains duplicates")
        if any(not TOOL_NAME.fullmatch(name) for name in self.allowed_tools):
            raise ValueError("allowed_tools contains an invalid tool name")
        if self.auto_connect:
            raise ValueError("auto_connect must remain false in stage 5")
        if self.transport == "stdio":
            self._validate_stdio()
        else:
            self._validate_http()
        return self

    def _validate_stdio(self) -> None:
        if not self.command or not self.command.strip():
            raise ValueError("stdio server requires command")
        if self.url is not None:
            raise ValueError("stdio server must not define url")
        if any(not ENV_NAME.fullmatch(name) for name in self.environment_allowlist):
            raise ValueError("environment_allowlist contains an invalid name")
        if any(SENSITIVE_ARGUMENT.search(value) for value in [self.command, *self.args]):
            raise ValueError("Sensitive values must not be stored in command arguments")

        cwd = self.resolved_cwd
        if not cwd.is_relative_to(self.project_root.resolve()):
            raise ValueError("stdio cwd must stay inside the project root")
        command_path = Path(self.command)
        has_path = command_path.is_absolute() or "/" in self.command or "\\" in self.command
        if has_path:
            resolved = command_path if command_path.is_absolute() else cwd / command_path
            resolved = resolved.resolve()
            if not resolved.is_relative_to(self.project_root.resolve()):
                raise ValueError("stdio command path must stay inside the project root")
        elif not BARE_COMMAND.fullmatch(self.command):
            raise ValueError("stdio command name is invalid")

    def _validate_http(self) -> None:
        if not self.url:
            raise ValueError("streamable_http server requires url")
        if self.command is not None or self.args:
            raise ValueError("streamable_http server must not define a command")
        parsed = urlparse(self.url)
        host = (parsed.hostname or "").lower()
        allowed = {item.lower() for item in self.allowed_hosts}
        localhost = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and localhost):
            raise ValueError("Remote MCP URLs must use HTTPS; HTTP is limited to localhost")
        if not host or host not in allowed:
            raise ValueError("Remote MCP host is not in allowed_hosts")
        if parsed.username or parsed.password:
            raise ValueError("Credentials must not be embedded in MCP URLs")
        if self.auth_env_var and not ENV_NAME.fullmatch(self.auth_env_var):
            raise ValueError("auth_env_var is invalid")

    @property
    def resolved_cwd(self) -> Path:
        value = Path(self.cwd or ".")
        return (value if value.is_absolute() else self.project_root / value).resolve()

    @property
    def resolved_command(self) -> str:
        assert self.command is not None
        path = Path(self.command)
        if path.is_absolute() or "/" in self.command or "\\" in self.command:
            return str((path if path.is_absolute() else self.resolved_cwd / path).resolve())
        return self.command


class MCPConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: list[MCPServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        return self


def load_mcp_configuration(settings: Settings, *, project_root: Path = BASE_DIR) -> MCPConfiguration:
    path = settings.mcp_config_path.resolve()
    root = project_root.resolve()
    if not path.is_relative_to(root):
        raise MCPIntegrationError("mcp_server_not_configured", "MCP config path must stay inside the project")
    if not path.exists():
        raise MCPIntegrationError("mcp_server_not_configured", "MCP config file does not exist")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MCPIntegrationError("mcp_server_not_configured", "MCP config file is invalid") from exc
    if raw is None:
        raw = {"servers": []}
    if not isinstance(raw, dict) or not isinstance(raw.get("servers", []), list):
        raise MCPIntegrationError("mcp_server_not_configured", "MCP config root is invalid")
    _reject_inline_secrets(raw)
    prepared = dict(raw)
    prepared["servers"] = [dict(server, project_root=root) if isinstance(server, dict) else server for server in raw.get("servers", [])]
    try:
        return MCPConfiguration.model_validate(prepared)
    except Exception as exc:
        raise MCPIntegrationError("mcp_server_not_configured", "MCP config validation failed") from exc


def _reject_inline_secrets(value: object, *, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            normalized = str(child_key).lower().replace("-", "_")
            if normalized in {"token", "password", "passwd", "api_key", "secret", "authorization"}:
                raise MCPIntegrationError("mcp_server_not_configured", "MCP config contains an inline secret")
            _reject_inline_secrets(child, key=normalized)
    elif isinstance(value, list):
        for child in value:
            _reject_inline_secrets(child, key=key)
    elif isinstance(value, str) and key not in {"auth_env_var", "environment_allowlist"}:
        if re.search(r"(?i)(?:password|api[-_]?key|token|secret)\s*[=:]", value):
            raise MCPIntegrationError("mcp_server_not_configured", "MCP config contains an inline secret")
