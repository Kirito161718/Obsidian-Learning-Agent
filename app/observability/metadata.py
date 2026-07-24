from __future__ import annotations

from collections import Counter
from typing import Any

from app.security.redaction import stable_hash

REFERENCE_TERMS = ("这个", "那个", "它", "上面", "前面", "刚才", "该内容")


def query_metadata(query: str) -> dict[str, Any]:
    chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
    latin = any(char.isascii() and char.isalpha() for char in query)
    language = "mixed" if chinese and latin else "zh" if chinese else "en" if latin else "unknown"
    return {
        "query_hash": stable_hash(query),
        "query_char_count": len(query),
        "query_language": language,
        "has_reference_expression": any(term in query for term in REFERENCE_TERMS),
    }


def rag_top_level_metadata(
    *,
    endpoint_type: str,
    internal_run_id: str | None,
    session_id: str | None,
    question: str,
) -> dict[str, Any]:
    return {
        "flow_type": "rag",
        "endpoint_type": endpoint_type,
        "internal_run_id": internal_run_id,
        "session_id": session_id,
        "status": "running",
        **query_metadata(question),
    }


def rag_result_metadata(state: dict[str, Any]) -> dict[str, Any]:
    chunks = list(state.get("context_chunks") or [])
    sources = list(state.get("context_sources") or [])
    evaluation = state.get("evaluation_result") or {}
    return {
        "qa_history_id": state.get("qa_history_id"),
        "rewrite_triggered": bool(state.get("rewrite_applied")),
        "rewrite_reason_code": state.get("rewrite_reason"),
        "original_retrieval_count": len(state.get("original_results") or []),
        "rewritten_retrieval_count": len(state.get("rewritten_results") or []),
        "merged_count": len(state.get("merged_results") or []),
        "rerank_input_count": len(state.get("expanded_results") or []),
        "rerank_output_count": len(state.get("reranked_results") or []),
        "context_chunk_count": len(chunks),
        "context_note_count": len({getattr(chunk, "source_id", None) for chunk in chunks}),
        "context_char_count": len(state.get("context_text") or ""),
        "citation_count": len(state.get("citations") or []),
        "citation_validation_passed": not bool(evaluation.get("invalid_citations")),
        "no_answer": not bool(state.get("answerable")) if state.get("mode") == "ask" else False,
        "model_call_count": int(state.get("model_call_count") or 0),
        "tool_call_count": int(state.get("tool_call_count") or 0),
        "error_code": state.get("error_code"),
        "status": "succeeded",
        "source_count": len(sources),
        "answer_policy": state.get("answer_policy"),
        "evidence_confidence_band": state.get(
            "evidence_confidence_band"
        ),
        "evidence_reason_codes": list(
            state.get("evidence_reason_codes") or []
        ),
        "evidence_feature_version": state.get(
            "evidence_feature_version"
        ),
        "citation_gate_passed": state.get("citation_gate_passed"),
        "abstained_before_llm": bool(
            state.get("abstained_before_llm")
        ),
        "uncertainty_answer": bool(state.get("uncertainty_answer")),
        "cold_start": bool(state.get("cold_start")),
        "cache_hit": bool(state.get("cache_hit")),
    }


def rag_node_metadata(
    span_name: str,
    state: dict[str, Any],
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    combined = {**state, **(output or {})}
    base: dict[str, Any] = {
        "flow_type": "rag_node",
        "node_name": span_name,
        "internal_run_id": state.get("run_id"),
        "session_id": state.get("session_id"),
        "status": "succeeded" if output is not None else "running",
        "safe_input_stats": _state_stats(state),
    }
    if output is not None:
        base["safe_output_stats"] = _state_stats(output)
    if span_name in {"ola.rag.retrieve.original", "ola.rag.retrieve.rewritten"}:
        query = (
            combined.get("rewritten_query")
            if span_name.endswith("rewritten")
            else combined.get("original_query")
        ) or ""
        results = (
            combined.get("rewritten_results")
            if span_name.endswith("rewritten")
            else combined.get("original_results")
        ) or []
        scores = [float(getattr(item, "score", 0.0) or 0.0) for item in results]
        base.update(
            {
                **query_metadata(query),
                "top_k": int(state.get("top_k") or 0),
                "filter_count": sum(
                    bool(value)
                    for value in (
                        state.get("document_id"),
                        state.get("source_types"),
                        state.get("source_path_prefixes"),
                    )
                ),
                "candidate_count": len(results),
                "source_type_counts": dict(
                    Counter(str(getattr(item, "source_type", "unknown")) for item in results)
                ),
                "score_min": min(scores) if scores else None,
                "score_max": max(scores) if scores else None,
                "score_avg": sum(scores) / len(scores) if scores else None,
            }
        )
    if span_name == "ola.memory.session.load":
        memory = combined.get("session_memory") or []
        base.update(
            {
                "memory_enabled": bool(state.get("session_id")),
                "message_count": len(memory),
                "total_char_count": sum(
                    len(str(item.get("question") or "")) + len(str(item.get("answer_preview") or ""))
                    for item in memory
                    if isinstance(item, dict)
                ),
                "truncated": bool(combined.get("session_memory_truncated")),
                "used_for_rewrite": bool(memory),
            }
        )
    if span_name == "ola.rag.rewrite.decide":
        quality = combined.get("retrieval_quality") or {}
        base.update(
            {
                "rewrite_triggered": bool(quality.get("rewrite_needed")),
                "rewrite_reason_code": quality.get("reason"),
            }
        )
    if span_name == "ola.rag.rewrite.generate":
        base.update(
            {
                "rewrite_triggered": bool(combined.get("rewrite_applied")),
                "rewrite_reason_code": combined.get("rewrite_reason"),
            }
        )
    if span_name == "ola.rag.retrieve.merge":
        base["merged_count"] = len(combined.get("merged_results") or [])
    if span_name == "ola.rag.rerank":
        base.update(
            {
                "rerank_input_count": len(state.get("expanded_results") or []),
                "rerank_output_count": len(combined.get("reranked_results") or []),
            }
        )
    if span_name == "ola.rag.context.build":
        chunks = combined.get("context_chunks") or []
        base.update(
            {
                "context_chunk_count": len(chunks),
                "context_note_count": len({getattr(item, "source_id", None) for item in chunks}),
                "context_char_count": len(combined.get("context_text") or ""),
            }
        )
    if span_name == "ola.rag.citations.validate":
        invalid = (combined.get("evaluation_result") or {}).get("invalid_citations") or []
        base.update(
            {
                "citation_count": len(combined.get("citations") or []),
                "citation_validation_passed": not bool(invalid),
            }
        )
    if span_name in {
        "ola.rag.evidence.evaluate",
        "ola.rag.answer_policy.route",
        "ola.rag.safety.enforce",
    }:
        base.update(
            {
                "answer_policy": combined.get("answer_policy"),
                "evidence_confidence_band": combined.get(
                    "evidence_confidence_band"
                ),
                "evidence_reason_codes": list(
                    combined.get("evidence_reason_codes") or []
                ),
                "evidence_feature_version": combined.get(
                    "evidence_feature_version"
                ),
                "citation_gate_passed": combined.get(
                    "citation_gate_passed"
                ),
                "abstained_before_llm": bool(
                    combined.get("abstained_before_llm")
                ),
                "uncertainty_answer": bool(
                    combined.get("uncertainty_answer")
                ),
            }
        )
    return base


def llm_usage_metadata(response: Any, *, model_name: str, retry_count: int = 0) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    choice = (getattr(response, "choices", None) or [None])[0]
    if usage is None:
        token_data: dict[str, Any] = {"token_usage_status": "unavailable"}
    else:
        token_data = {
            "input_token_count": getattr(usage, "prompt_tokens", None),
            "output_token_count": getattr(usage, "completion_tokens", None),
            "total_token_count": getattr(usage, "total_tokens", None),
            "token_usage_status": "available",
        }
    return {
        "ls_provider": "deepseek",
        "ls_model_name": model_name,
        "model_name": model_name,
        "finish_reason": getattr(choice, "finish_reason", None),
        "retry_count": retry_count,
        **token_data,
    }


def _state_stats(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (list, tuple, set, dict, str)):
            result[f"{key}_count" if not isinstance(item, str) else f"{key}_char_count"] = len(item)
        elif isinstance(item, (bool, int, float)) or item is None:
            result[key] = item
    return result
