import re

from app.core.config import Settings, get_settings
from app.tools.definitions import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        if not re.fullmatch(r"(?:[A-Za-z0-9_]+|mcp\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", definition.name):
            raise ValueError("Tool name is invalid")
        self._tools[definition.name] = definition

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def unregister_provider(self, provider_name: str) -> None:
        names = [
            name
            for name, definition in self._tools.items()
            if definition.tool_source == "mcp" and definition.provider_name == provider_name
        ]
        for name in names:
            self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self) -> list[ToolDefinition]:
        return [self._tools[name] for name in sorted(self._tools)]


def build_tool_registry(settings: Settings | None = None) -> ToolRegistry:
    from app.tools.internal.tools import internal_tool_definitions

    settings = settings or get_settings()
    registry = ToolRegistry()
    for definition in internal_tool_definitions(settings):
        registry.register(definition)
    return registry


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_tool_registry()
    return _registry
