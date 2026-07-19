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

    def metadata(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "enabled": self.enabled,
            "idempotent": self.idempotent,
            "timeout_seconds": self.timeout_seconds,
            "allowed_callers": sorted(caller.value for caller in self.allowed_callers),
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": self.output_schema.model_json_schema(),
        }
