from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel


class RiskLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    HIGH_RISK = "HIGH_RISK"


class CallerType(str, Enum):
    user = "user"
    system = "system"
    model = "model"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    risk_level: RiskLevel
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    callable: Callable[..., Any]
    enabled: bool
    idempotent: bool
    timeout_seconds: int
    allowed_callers: frozenset[CallerType]
    tool_source: str = "internal"
    provider_name: str | None = None
    remote_tool_name: str | None = None
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "enabled": self.enabled,
            "idempotent": self.idempotent,
            "timeout_seconds": self.timeout_seconds,
            "allowed_callers": sorted(caller.value for caller in self.allowed_callers),
            "input_schema": self.input_json_schema or self.input_schema.model_json_schema(),
            "output_schema": self.output_json_schema or self.output_schema.model_json_schema(),
            "tool_source": self.tool_source,
            "provider_name": self.provider_name,
            "remote_tool_name": self.remote_tool_name,
        }
