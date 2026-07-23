from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from app.observability.metadata import rag_node_metadata


def observe_graph_node(name: str, function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(function)
    def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        from app.observability import get_observability

        manager = get_observability()
        with manager.trace(
            name=name,
            run_type="chain",
            metadata=rag_node_metadata(name, state),
            tags=["ola", "graph-node", name],
        ) as span:
            output = function(state, *args, **kwargs)
            span.update(rag_node_metadata(name, state, output))
            return output

    return wrapped


def observe_official_node(name: str, function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(function)
    def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        from app.observability import get_observability

        metadata = {
            "flow_type": "official_node",
            "node_name": name,
            "internal_run_id": state.get("run_id"),
            "official_source_id": state.get("source_id"),
            "snapshot_id": state.get("snapshot_id"),
            "proposal_id": state.get("proposal_id"),
            "approval_request_id": state.get("approval_request_id"),
            "checkpoint_thread_id": state.get("proposal_id"),
            "status": "running",
            "item_count": int(state.get("item_count") or 0),
        }
        manager = get_observability()
        with manager.trace(
            name=name,
            run_type="chain",
            metadata=metadata,
            tags=["ola", "official", name],
        ) as span:
            output = function(state, *args, **kwargs)
            span.update(
                {
                    "status": output.get("status") or "succeeded",
                    "item_count": int(output.get("item_count") or state.get("item_count") or 0),
                    "applied_count": int(output.get("applied_count") or 0),
                    "reindex_status": output.get("reindex_status"),
                    "approval_request_id": output.get("approval_request_id")
                    or state.get("approval_request_id"),
                }
            )
            return output

    return wrapped
