from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.rag_evaluation.metrics import retrieval_identity
from app.rag_evaluation.schemas import EvaluationCase, EvaluationRunOutput


FAILURE_RECOMMENDATIONS = {
    "retrieval_miss": ["inspect_chunk_v2", "inspect_query_rewrite"],
    "lexical_miss": ["assess_hybrid_feasibility"],
    "semantic_miss": ["inspect_chunk_v2", "inspect_query_scope"],
    "wrong_scope": ["inspect_payload_filters"],
    "chunk_boundary_loss": ["inspect_chunk_v2", "assess_neighbor_expansion"],
    "chunk_too_small": ["assess_parent_child", "assess_neighbor_expansion"],
    "chunk_too_large": ["inspect_chunk_v2"],
    "duplicate_candidates": ["assess_mmr", "deduplicate_candidates"],
    "same_note_overrepresentation": ["enforce_note_cap", "assess_mmr"],
    "cross_note_redundancy": ["assess_mmr"],
    "link_expansion_noise": ["tighten_link_expansion"],
    "missing_link_context": ["inspect_link_expansion_v2"],
    "rewrite_not_triggered": ["inspect_rewrite_trigger"],
    "rewrite_hurt": ["tighten_rewrite_trigger"],
    "rerank_inversion": ["tune_rerank_candidate_text"],
    "context_truncation": ["inspect_context_budget"],
    "citation_invalid": ["inspect_citation_validation"],
    "false_answer": ["tighten_answerability"],
    "false_refusal": ["inspect_answerability_threshold"],
    "conflicting_sources": ["preserve_conflict_context"],
}


def classify_failures(
    case: EvaluationCase,
    output: EvaluationRunOutput,
    metrics: dict[str, Any],
    *,
    max_chunks_per_note: int = 3,
) -> list[str]:
    failures: list[str] = []
    hit = metrics.get("hit_rate@10")
    if case.should_answer and hit == 0:
        failures.append("retrieval_miss")
        retrieved_note_ids = {
            retrieval_identity(item)[0] for item in output.retrieved
        }
        expected_note_ids = {
            item.note_id
            for item in case.relevance_judgments
            if item.relevance_grade > 0
        }
        if retrieved_note_ids & expected_note_ids:
            failures.append("chunk_boundary_loss")
        else:
            failures.append("semantic_miss")
    if output.metadata.get("scope_violation"):
        failures.append("wrong_scope")
    identities = [retrieval_identity(item) for item in output.retrieved]
    if len(identities) != len(set(identities)):
        failures.append("duplicate_candidates")
    note_counts = Counter(note_id for note_id, _ in identities if note_id)
    if note_counts and max(note_counts.values()) > max_chunks_per_note:
        failures.append("same_note_overrepresentation")
    lengths = [
        len(str((item.get("payload") or item).get("text") or ""))
        for item in output.retrieved
        if isinstance(item, dict)
    ]
    if lengths and sum(length < 200 for length in lengths) > len(lengths) / 2:
        failures.append("chunk_too_small")
    if lengths and any(length > 6000 for length in lengths):
        failures.append("chunk_too_large")
    if case.category == "internal_link" and hit == 0:
        failures.append("missing_link_context")
    if case.requires_rewrite and not output.rewrite_triggered:
        failures.append("rewrite_not_triggered")
    if output.original_query_hit and output.rewritten_query_hit is False:
        failures.append("rewrite_hurt")
    if (
        metrics.get("pre_rerank_hit") == 1
        and metrics.get("post_rerank_hit") == 0
    ) or (
        isinstance(metrics.get("pre_rerank_ndcg"), (int, float))
        and isinstance(metrics.get("post_rerank_ndcg"), (int, float))
        and metrics["post_rerank_ndcg"] + 0.05 < metrics["pre_rerank_ndcg"]
    ):
        failures.append("rerank_inversion")
    if output.metadata.get("context_truncated"):
        failures.append("context_truncation")
    if metrics.get("citation_source_validity") is not None and (
        metrics.get("citation_source_validity", 1) < 1 or metrics.get(
        "unsupported_citation_count", 0
        )
    ):
        failures.append("citation_invalid")
    if metrics.get("false_answer_rate") == 1:
        failures.append("false_answer")
    if metrics.get("false_refusal_rate") == 1:
        failures.append("false_refusal")
    if case.category == "conflicting_sources" and metrics.get(
        "answer_point_coverage"
    ) not in {None, 1.0}:
        failures.append("conflicting_sources")
    if output.metadata.get("link_expansion_noise"):
        failures.append("link_expansion_noise")
    return list(dict.fromkeys(failures))


def summarize_failures(
    case_failures: list[tuple[str, list[str]]],
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    case_ids: dict[str, list[str]] = defaultdict(list)
    for case_id, failures in case_failures:
        for failure in failures:
            counts[failure] += 1
            case_ids[failure].append(case_id)
    return {
        "counts": dict(sorted(counts.items())),
        "case_ids": {key: sorted(values) for key, values in sorted(case_ids.items())},
        "recommendations": {
            key: FAILURE_RECOMMENDATIONS.get(key, [])
            for key in sorted(counts)
        },
    }
