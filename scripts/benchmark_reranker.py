"""Small, read-only CPU/CUDA reranker comparison for local operations."""

from __future__ import annotations

import gc
import json
from pathlib import Path
import sys
import time

import torch
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.services.embedding_service import get_embedding_service
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService


def main() -> None:
    settings = get_settings()
    dataset_path = (
        settings.rag_evaluation_dataset_dir / "obsidian-real-v2.yaml"
    )
    dataset = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    answerable = [
        item for item in dataset["cases"] if item.get("should_answer")
    ][:4]
    no_answer = next(
        item for item in dataset["cases"] if not item.get("should_answer")
    )
    cases = answerable + [no_answer]

    started = time.perf_counter()
    embedding = get_embedding_service()
    embedding_initialization_ms = (time.perf_counter() - started) * 1000
    vector = VectorService(ensure_collection=False)
    candidate_sets = []
    embedding_times = []
    qdrant_times = []
    for case in cases:
        started = time.perf_counter()
        query_vector = embedding.embed_query(case["question"])
        embedding_times.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        candidate_sets.append(
            vector.search(
                query_vector,
                top_k=20,
                source_types=["obsidian_note"],
            )
        )
        qdrant_times.append((time.perf_counter() - started) * 1000)
    print("Candidates prepared; starting CPU comparison.", flush=True)

    cpu = benchmark_device(
        "cpu", settings.rerank_model, cases, candidate_sets
    )
    print("CPU comparison complete; starting CUDA comparison.", flush=True)
    cuda = benchmark_device(
        "cuda", settings.rerank_model, cases, candidate_sets
    )
    exact = [
        left == right
        for left, right in zip(
            cpu.pop("rankings"),
            cuda.pop("rankings"),
            strict=True,
        )
    ]
    speedup = (
        (1 - cuda["warm_mean_per_case_ms"] / cpu["warm_mean_per_case_ms"])
        * 100
        if cpu["warm_mean_per_case_ms"]
        else 0
    )
    print(
        "STAGE9_BENCHMARK="
        + json.dumps(
            {
                "case_ids": [item["id"] for item in cases],
                "candidate_counts": [
                    len(items) for items in candidate_sets
                ],
                "embedding_initialization_ms": round(
                    embedding_initialization_ms, 2
                ),
                "query_embedding_mean_ms": round(
                    sum(embedding_times) / len(embedding_times), 2
                ),
                "qdrant_mean_ms": round(
                    sum(qdrant_times) / len(qdrant_times), 2
                ),
                "cpu": cpu,
                "cuda": cuda,
                "ranking_exact_by_case": exact,
                "warm_speedup_percent": round(speedup, 2),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def benchmark_device(
    device: str,
    model_name: str,
    cases: list[dict],
    candidate_sets: list[list[dict]],
) -> dict:
    cuda_measurement = device == "cuda" and torch.cuda.is_available()
    if cuda_measurement:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    service = RerankService(
        enabled=True,
        model_name=model_name,
        device=device,
        batch_size=8,
    )
    started = time.perf_counter()
    first = [
        service.rerank(case["question"], chunks, top_n=5)
        for case, chunks in zip(cases, candidate_sets, strict=True)
    ]
    del first
    cold_total_ms = (time.perf_counter() - started) * 1000
    initialization_ms = service.consume_initialization_ms()
    warm_rounds = []
    last = []
    for _ in range(3):
        started = time.perf_counter()
        last = [
            service.rerank(case["question"], chunks, top_n=5)
            for case, chunks in zip(cases, candidate_sets, strict=True)
        ]
        warm_rounds.append((time.perf_counter() - started) * 1000)
    status = service.runtime_status()
    result = {
        "configured": device,
        "status": status,
        "initialization_ms": round(initialization_ms, 2),
        "cold_total_5_cases_ms": round(cold_total_ms, 2),
        "warm_rounds_5_cases_ms": [
            round(value, 2) for value in warm_rounds
        ],
        "warm_mean_per_case_ms": round(
            sum(warm_rounds) / len(warm_rounds) / len(cases), 2
        ),
        "cuda_active_mb": (
            round(torch.cuda.memory_allocated() / 1024 / 1024, 1)
            if cuda_measurement
            else 0
        ),
        "cuda_peak_mb": (
            round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)
            if cuda_measurement
            else 0
        ),
        "rankings": [
            [
                str(item.get("payload", {}).get("chunk_id"))
                for item in group
            ]
            for group in last
        ],
    }
    service.shutdown()
    del service
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return result


if __name__ == "__main__":
    main()
