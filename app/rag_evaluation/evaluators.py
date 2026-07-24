from __future__ import annotations

from app.rag_evaluation.metrics import (
    answerability_metrics,
    citation_metrics,
    expected_point_coverage,
    retrieval_metrics,
)
from app.rag_evaluation.schemas import EvaluationCase, EvaluationRunOutput


def evaluate_output(
    case: EvaluationCase,
    output: EvaluationRunOutput,
    *,
    k_values: tuple[int, ...] = (5, 10, 20),
    include_answer_metrics: bool = True,
) -> dict[str, float | int | bool | str | None]:
    metrics: dict[str, float | int | bool | str | None] = {}
    metrics.update(
        retrieval_metrics(
            output.retrieved,
            case.relevance_judgments,
            k_values=k_values,
        )
    )
    if include_answer_metrics:
        metrics.update(
            citation_metrics(
                answer=output.answer,
                citations=output.citations,
                acceptable_note_ids=case.acceptable_citation_note_ids,
                forbidden_note_ids=case.forbidden_citation_note_ids,
                retrieved=output.retrieved,
                should_answer=case.should_answer,
            )
        )
        predicted_answerable = (
            bool(output.answerable)
            if output.answerable is not None
            else bool(output.answer and output.retrieved)
        )
        metrics.update(
            answerability_metrics(
                should_answer=case.should_answer,
                predicted_answerable=predicted_answerable,
            )
        )
        expected_policy = case.expected_policy
        actual_policy = output.metadata.get("answer_policy")
        if expected_policy is not None:
            metrics["policy_correct"] = float(
                expected_policy == actual_policy
            )
            metrics["expected_policy"] = expected_policy
            metrics["answer_policy"] = actual_policy
    metrics.update(
        {
            "answer_point_coverage": (
                expected_point_coverage(output.answer, case.expected_answer_points)
                if include_answer_metrics
                else None
            ),
            "rewrite_expected": case.requires_rewrite,
            "rewrite_triggered": output.rewrite_triggered,
            "rewrite_decision_correct": (
                case.requires_rewrite == output.rewrite_triggered
            ),
            "original_query_hit": output.original_query_hit,
            "rewritten_query_hit": output.rewritten_query_hit,
            "dual_retrieval_gain": (
                float(bool(output.rewritten_query_hit))
                - float(bool(output.original_query_hit))
                if output.original_query_hit is not None
                and output.rewritten_query_hit is not None
                else None
            ),
            "rewrite_latency_ms": output.rewrite_latency_ms,
            "retrieval_latency_ms": output.retrieval_latency_ms,
            "rerank_latency_ms": output.rerank_latency_ms,
            "llm_latency_ms": output.llm_latency_ms,
            "total_latency_ms": output.total_latency_ms,
            "model_call_count": output.model_call_count,
            "rewrite_call_count": output.rewrite_call_count,
            "input_token_count": (
                output.input_token_count
                if output.input_token_count is not None
                else "unavailable"
            ),
            "output_token_count": (
                output.output_token_count
                if output.output_token_count is not None
                else "unavailable"
            ),
        }
    )
    for key, value in dict(
        output.metadata.get("performance_metrics") or {}
    ).items():
        if isinstance(value, (int, float, bool)):
            metrics[f"stage_{key}"] = value
    for key in (
        "abstained_before_llm",
        "uncertainty_answer",
        "citation_gate_passed",
        "cold_start",
        "cache_hit",
    ):
        value = output.metadata.get(key)
        if isinstance(value, (int, float, bool)):
            metrics[key] = value
    pre_metrics = retrieval_metrics(
        output.pre_rerank,
        case.relevance_judgments,
        k_values=(10,),
    )
    post_metrics = retrieval_metrics(
        output.retrieved,
        case.relevance_judgments,
        k_values=(10,),
    )
    metrics.update(
        {
            "pre_rerank_hit": pre_metrics["hit_rate@10"],
            "post_rerank_hit": post_metrics["hit_rate@10"],
            "pre_rerank_ndcg": pre_metrics["ndcg@10"],
            "post_rerank_ndcg": post_metrics["ndcg@10"],
            "relevant_rank_delta": (
                (post_metrics["mrr"] or 0) - (pre_metrics["mrr"] or 0)
                if pre_metrics["mrr"] is not None
                and post_metrics["mrr"] is not None
                else None
            ),
        }
    )
    return metrics
