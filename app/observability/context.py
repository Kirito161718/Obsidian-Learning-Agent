from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SpanHandle:
    name: str
    run_type: str
    external_trace_id: str | None
    parent_trace_id: str | None
    status: str
    backend_span: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _manager: Any | None = None

    @property
    def enabled(self) -> bool:
        return self.backend_span is not None

    def update(self, metadata: dict[str, Any] | None = None, *, tags: list[str] | None = None) -> None:
        if self._manager is not None:
            self._manager.update_span(self, metadata=metadata, tags=tags)


CURRENT_SPAN: ContextVar[SpanHandle | None] = ContextVar("ola_observability_span", default=None)
