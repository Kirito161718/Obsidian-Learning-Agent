from app.core.config import Settings, get_settings
from app.tools.definitions import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        if not definition.name or not definition.name.replace("_", "").isalnum():
            raise ValueError("Tool name must contain only letters, numbers, and underscores")
        self._tools[definition.name] = definition

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
