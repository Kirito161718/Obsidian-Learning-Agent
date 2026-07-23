from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class FakeSpan:
    id: str
    trace_id: str
    name: str
    run_type: str
    parent_run_id: str | None
    metadata: dict[str, Any]
    tags: list[str]
    finished: bool = False

    def add_metadata(self, metadata: dict[str, Any]) -> None:
        self.metadata.update(metadata)

    def add_tags(self, tags: list[str]) -> None:
        self.tags.extend(tag for tag in tags if tag not in self.tags)


@dataclass(slots=True)
class FakeCollector:
    spans: list[FakeSpan] = field(default_factory=list)
    fail_open: bool = False
    fail_flush: bool = False
    flush_count: int = 0

    @contextmanager
    def open_span(self, *, name, run_type, metadata, tags, parent):
        if self.fail_open:
            raise RuntimeError("synthetic collector failure")
        span_id = str(uuid4())
        trace_id = parent.trace_id if parent is not None else span_id
        span = FakeSpan(
            id=span_id,
            trace_id=trace_id,
            name=name,
            run_type=run_type,
            parent_run_id=parent.id if parent is not None else None,
            metadata=dict(metadata),
            tags=list(tags),
        )
        self.spans.append(span)
        try:
            yield span
        finally:
            span.finished = True

    def flush(self, timeout: float) -> None:
        del timeout
        self.flush_count += 1
        if self.fail_flush:
            raise TimeoutError("synthetic flush timeout")
