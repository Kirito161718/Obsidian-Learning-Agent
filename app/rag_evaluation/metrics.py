from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any, Iterable

from app.rag_evaluation.schemas import EvaluationCase, RelevanceJudgment


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else item


def retrieval_identity(item: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    payload = _payload(item)
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    note_id = str(
        payload.get("source_id")
        or payload.get("note_id")
        or payload.get("document_id")
        or ""
    )
    raw_heading = payload.get("heading_path", metadata.get("heading_path", []))
    heading = (
        tuple(str(part).strip() for part in raw_heading if str(part).strip())
        if isinstance(raw_heading, (list, tuple))
        else ()
    )
    return note_id, heading


def retrieval_heading_paths(item: dict[str, Any]) -> set[tuple[str, ...]]:
    payload = _payload(item)
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    chunk_metadata = payload.get("chunk_metadata")
    chunk_metadata = (
        chunk_metadata if isinstance(chunk_metadata, dict) else {}
    )
    _, primary = retrieval_identity(item)
    raw_paths = (
        metadata.get("merged_heading_paths")
        or chunk_metadata.get("merged_heading_paths")
        or []
    )
    paths = {
        tuple(str(part).strip() for part in path if str(part).strip())
        for path in raw_paths
        if isinstance(path, (list, tuple))
    }
    if primary:
        paths.add(primary)
    return paths or {()}


def dedupe_ranked(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in items:
        identity = retrieval_identity(item)
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def relevance_grade(
    item: dict[str, Any],
    judgments: list[RelevanceJudgment],
) -> int:
    note_id, _ = retrieval_identity(item)
    headings = retrieval_heading_paths(item)
    grades: list[int] = []
    for judgment in judgments:
        expected = tuple(judgment.heading_path)
        if judgment.note_id != note_id:
            continue
        if not expected or expected in headings:
            grades.append(judgment.relevance_grade)
    return max(grades, default=0)


def retrieval_metrics(
    ranked_items: list[dict[str, Any]],
    judgments: list[RelevanceJudgment],
    *,
    k_values: tuple[int, ...] = (5, 10, 20),
) -> dict[str, float | None]:
    positive = [item for item in judgments if item.relevance_grade > 0]
    if not positive:
        metrics: dict[str, float | None] = {"mrr": None}
        for k in k_values:
            metrics.update(
                {
                    f"hit_rate@{k}": None,
                    f"recall@{k}": None,
                    f"precision@{k}": None,
                    f"ndcg@{k}": None,
                }
            )
        return metrics

    ranked = dedupe_ranked(ranked_items)
    relevant_identities = {
        (item.note_id, tuple(item.heading_path)) for item in positive
    }

    def matched_ground_truth(items: list[dict[str, Any]]) -> set[tuple[str, tuple[str, ...]]]:
        matched: set[tuple[str, tuple[str, ...]]] = set()
        for result in items:
            note_id, _ = retrieval_identity(result)
            headings = retrieval_heading_paths(result)
            for judgment in positive:
                expected = tuple(judgment.heading_path)
                if note_id == judgment.note_id and (
                    not expected or expected in headings
                ):
                    matched.add((judgment.note_id, expected))
        return matched

    credited: set[tuple[str, tuple[str, ...]]] = set()
    ranked_grades: list[int] = []
    for item in ranked:
        note_id, _ = retrieval_identity(item)
        headings = retrieval_heading_paths(item)
        matches = [
            judgment
            for judgment in positive
            if judgment.note_id == note_id
            and (
                not judgment.heading_path
                or tuple(judgment.heading_path) in headings
            )
            and (judgment.note_id, tuple(judgment.heading_path)) not in credited
        ]
        if not matches:
            ranked_grades.append(0)
            continue
        best = max(matches, key=lambda judgment: judgment.relevance_grade)
        credited.add((best.note_id, tuple(best.heading_path)))
        ranked_grades.append(best.relevance_grade)
    first_relevant_rank = next(
        (
            index
            for index, grade in enumerate(ranked_grades, start=1)
            if grade > 0
        ),
        None,
    )
    result = {
        "mrr": 1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
    }
    ideal_grades = sorted(
        (item.relevance_grade for item in positive),
        reverse=True,
    )
    for k in k_values:
        top = ranked[:k]
        grades = ranked_grades[:k]
        relevant_count = sum(grade > 0 for grade in grades)
        matched = matched_ground_truth(top)
        dcg = sum(
            ((2**grade) - 1) / math.log2(index + 1)
            for index, grade in enumerate(grades, start=1)
            if grade > 0
        )
        idcg = sum(
            ((2**grade) - 1) / math.log2(index + 1)
            for index, grade in enumerate(ideal_grades[:k], start=1)
        )
        result.update(
            {
                f"hit_rate@{k}": 1.0 if relevant_count else 0.0,
                f"recall@{k}": len(matched) / len(relevant_identities),
                f"precision@{k}": relevant_count / len(top) if top else 0.0,
                f"ndcg@{k}": dcg / idcg if idcg else None,
            }
        )
    return result


def citation_metrics(
    *,
    answer: str | None,
    citations: list[dict[str, Any]],
    acceptable_note_ids: list[str],
    forbidden_note_ids: list[str],
    retrieved: list[dict[str, Any]],
    should_answer: bool,
) -> dict[str, float | int | None]:
    answer = answer or ""
    cited_ids = [
        retrieval_identity(item)[0]
        for item in citations
        if retrieval_identity(item)[0]
    ]
    retrieved_ids = {retrieval_identity(item)[0] for item in retrieved}
    acceptable = set(acceptable_note_ids)
    forbidden = set(forbidden_note_ids)
    valid_ids = [
        note_id
        for note_id in cited_ids
        if note_id in retrieved_ids and note_id not in forbidden
    ]
    supported_ids = [note_id for note_id in valid_ids if note_id in acceptable]
    unsupported = len(cited_ids) - len(supported_ids)
    format_valid = (
        1.0
        if not cited_ids
        or all(marker in answer for marker in (f"[来源{index}]" for index in range(1, len(cited_ids) + 1)))
        else 0.0
    )
    if not should_answer:
        precision = 1.0 if not cited_ids else 0.0
        recall = None
    else:
        precision = len(supported_ids) / len(cited_ids) if cited_ids else 0.0
        recall = (
            len(set(supported_ids)) / len(acceptable)
            if acceptable
            else (1.0 if not cited_ids else 0.0)
        )
    return {
        "citation_format_validity": format_valid,
        "citation_source_validity": (
            sum(note_id in retrieved_ids and note_id not in forbidden for note_id in cited_ids)
            / len(cited_ids)
            if cited_ids
            else (1.0 if not should_answer else 0.0)
        ),
        "citation_precision": precision,
        "citation_recall": recall,
        "unsupported_citation_count": unsupported,
    }


def answerability_metrics(
    *, should_answer: bool, predicted_answerable: bool
) -> dict[str, float]:
    correct = should_answer == predicted_answerable
    return {
        "answerability_accuracy": float(correct),
        "no_answer_accuracy": (
            float(not predicted_answerable) if not should_answer else 1.0
        ),
        "false_answer_rate": float(not should_answer and predicted_answerable),
        "false_refusal_rate": float(should_answer and not predicted_answerable),
    }


def expected_point_coverage(answer: str | None, expected_points: list[str]) -> float | None:
    if not expected_points:
        return None
    normalized = "".join((answer or "").casefold().split())
    matched = sum(
        "".join(point.casefold().split()) in normalized for point in expected_points
    )
    return matched / len(expected_points)


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def aggregate_metric_rows(
    rows: list[dict[str, float | int | bool | str | None]],
) -> dict[str, float | int | str | None]:
    keys = sorted({key for row in rows for key in row})
    result: dict[str, float | int | str | None] = {"case_runs": len(rows)}
    for key in keys:
        values = [
            float(value)
            for row in rows
            if isinstance((value := row.get(key)), (bool, int, float))
            and math.isfinite(float(value))
        ]
        if values:
            result[key] = mean(values)
            if len(values) > 1:
                result[f"{key}_stddev"] = pstdev(values)
    latencies = [
        float(row["total_latency_ms"])
        for row in rows
        if isinstance(row.get("total_latency_ms"), (int, float))
    ]
    result["p50_latency"] = percentile(latencies, 0.50)
    result["p95_latency"] = percentile(latencies, 0.95)
    return result


def aggregate_repeat_metrics(
    rows: list[
        tuple[int, dict[str, float | int | bool | str | None]]
    ],
) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, float | int | bool | str | None]]] = {}
    for repeat_index, metrics in rows:
        grouped.setdefault(repeat_index, []).append(metrics)
    summaries = {
        repeat_index: aggregate_metric_rows(metrics)
        for repeat_index, metrics in sorted(grouped.items())
    }
    result: dict[str, Any] = {
        "repeat_count": len(summaries),
        "repeat_summaries": {
            str(index): summary for index, summary in summaries.items()
        },
    }
    keys = sorted(
        {
            key
            for summary in summaries.values()
            for key, value in summary.items()
            if isinstance(value, (bool, int, float))
            and key != "case_runs"
            and not key.endswith("_stddev")
        }
    )
    for key in keys:
        values = [
            float(summary[key])
            for summary in summaries.values()
            if isinstance(summary.get(key), (bool, int, float))
            and math.isfinite(float(summary[key]))
        ]
        if not values:
            continue
        result[f"{key}_repeat_mean"] = mean(values)
        result[f"{key}_repeat_stddev"] = (
            pstdev(values) if len(values) > 1 else 0.0
        )
    return result
