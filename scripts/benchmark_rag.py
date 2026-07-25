"""Minimal Full RAG cold/warm benchmark without QA or Qdrant writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import sys
import time

import yaml
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Performance probes keep all observability local even if .env enables tracing.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_OBSERVABILITY_ENABLED"] = "false"

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import QAHistory
from app.routers.rag import get_rag_service
from app.services.embedding_service import embedding_runtime_status
from app.services.vector_service import get_qdrant_client


def main() -> None:
    settings = get_settings()
    dataset = yaml.safe_load(
        (
            settings.rag_evaluation_dataset_dir
            / "obsidian-real-v2.yaml"
        ).read_text(encoding="utf-8")
    )
    answerable = [
        item for item in dataset["cases"] if item.get("should_answer")
    ][:3]
    no_answer = next(
        item for item in dataset["cases"] if not item.get("should_answer")
    )
    cases = answerable + [no_answer]
    db = SessionLocal()
    qa_before = int(db.scalar(select(func.count()).select_from(QAHistory)) or 0)
    points_before = int(
        get_qdrant_client()
        .get_collection(settings.qdrant_collection)
        .points_count
        or 0
    )

    service_started = time.perf_counter()
    service = get_rag_service()
    service_initialization_ms = (time.perf_counter() - service_started) * 1000
    profile = service._active_profile(db)

    cold_started = time.perf_counter()
    cold_state = invoke(service, db, profile, cases[0])
    cold_total_ms = (
        service_initialization_ms
        + (time.perf_counter() - cold_started) * 1000
    )
    print("Cold Full RAG case complete; starting three warm rounds.", flush=True)

    warm_rounds = []
    case_results = []
    for round_index in range(3):
        round_results = []
        started = time.perf_counter()
        for case in cases:
            state = invoke(service, db, profile, case)
            metrics = dict(state.get("performance_metrics") or {})
            round_results.append(
                {
                    "case_id": case["id"],
                    "expected_answerable": bool(case["should_answer"]),
                    "answer_policy": state.get("answer_policy"),
                    "citation_count": len(state.get("citations") or []),
                    "total_ms": round(float(metrics.get("total_ms") or 0), 2),
                    "query_embedding_ms": round(
                        float(metrics.get("query_embedding_ms") or 0), 2
                    ),
                    "qdrant_ms": round(
                        float(metrics.get("qdrant_original_ms") or 0)
                        + float(metrics.get("qdrant_rewritten_ms") or 0),
                        2,
                    ),
                    "rerank_ms": round(
                        float(metrics.get("rerank_ms") or 0), 2
                    ),
                    "deepseek_ms": round(
                        float(metrics.get("answer_llm_ms") or 0)
                        + float(metrics.get("rewrite_llm_ms") or 0),
                        2,
                    ),
                }
            )
        warm_rounds.append((time.perf_counter() - started) * 1000)
        case_results.extend(round_results)
        print(f"Warm round {round_index + 1}/3 complete.", flush=True)

    qa_after = int(db.scalar(select(func.count()).select_from(QAHistory)) or 0)
    points_after = int(
        get_qdrant_client()
        .get_collection(settings.qdrant_collection)
        .points_count
        or 0
    )
    db.close()
    print(
        "STAGE9_FULL_RAG="
        + json.dumps(
            {
                "case_ids": [item["id"] for item in cases],
                "cold_case_id": cases[0]["id"],
                "cold_total_ms": round(cold_total_ms, 2),
                "cold_metrics": cold_state.get("performance_metrics") or {},
                "embedding_initialization_ms": round(
                    float(
                        embedding_runtime_status().get(
                            "initialization_ms", 0
                        )
                    ),
                    2,
                ),
                "warm_rounds_4_cases_ms": [
                    round(value, 2) for value in warm_rounds
                ],
                "warm_total_mean_ms": round(
                    statistics.mean(
                        item["total_ms"] for item in case_results
                    ),
                    2,
                ),
                "warm_query_embedding_mean_ms": mean_metric(
                    case_results, "query_embedding_ms"
                ),
                "warm_qdrant_mean_ms": mean_metric(
                    case_results, "qdrant_ms"
                ),
                "warm_rerank_mean_ms": mean_metric(
                    case_results, "rerank_ms"
                ),
                "warm_deepseek_mean_ms": mean_metric(
                    case_results, "deepseek_ms"
                ),
                "policies": [
                    {
                        "case_id": item["case_id"],
                        "policy": item["answer_policy"],
                        "citations": item["citation_count"],
                    }
                    for item in case_results[-len(cases) :]
                ],
                "qa_unchanged": qa_before == qa_after,
                "qdrant_points_unchanged": points_before == points_after,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def invoke(service, db, profile: dict, case: dict) -> dict:
    return service.workflow.invoke(
        mode="ask",
        question=case["question"],
        document_id=None,
        top_k=5,
        db=db,
        rewrite_enabled=False,
        source_types=["obsidian_note"],
        evaluation_mode=True,
        retrieval_profile=profile,
    )


def mean_metric(items: list[dict], key: str) -> float:
    return round(statistics.mean(float(item[key]) for item in items), 2)


if __name__ == "__main__":
    main()
