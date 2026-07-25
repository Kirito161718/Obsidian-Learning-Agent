import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.graphs.rag_nodes import (
    CONFLICT_CITATION_FAILURE_ANSWER,
    RagNodes,
)
from app.services import rag_model_runtime, system_status, vector_service
from app.services.rerank_service import RerankService, get_rerank_service
from app.services.vector_service import VectorService


def test_reranker_auto_selects_cuda_when_available(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    service = RerankService(
        enabled=True,
        model_name="fixture",
        device="auto",
    )
    assert service._preferred_device() == "cuda"


def test_reranker_cuda_load_failure_falls_back_to_cpu(monkeypatch):
    service = RerankService(
        enabled=True,
        model_name="fixture",
        device="auto",
    )
    attempts = []
    monkeypatch.setattr(service, "_preferred_device", lambda: "cuda")
    monkeypatch.setattr(service, "_release_cuda_memory", lambda: None)

    def load(_model, device):
        attempts.append(device)
        if device == "cpu":
            service._reranker = object()
            service._active_device = "cpu"
            service._status = "ready"
            return True
        return False

    monkeypatch.setattr(service, "_load_on_device", load)
    assert service._load_reranker() is service._reranker
    assert attempts == ["cuda", "cpu"]
    assert service.runtime_status()["fallback"] is True


def test_reranker_scoring_failure_retries_on_cpu(monkeypatch):
    service = RerankService(
        enabled=True,
        model_name="fixture",
        device="auto",
    )
    service._reranker = object()
    service._active_device = "cuda"
    service._backend = "CrossEncoder"
    service._load_attempted = True
    calls = []

    def score(_reranker, pairs):
        calls.append(service._active_device)
        if service._active_device == "cuda":
            raise RuntimeError("CUDA OOM")
        return [0.9 for _ in pairs]

    def fallback(_exc):
        service._active_device = "cpu"
        service._reranker = object()
        return True

    monkeypatch.setattr(service, "_score_pairs", score)
    monkeypatch.setattr(service, "_fallback_to_cpu", fallback)
    result = service.rerank(
        "query",
        [{"payload": {"text": "context"}}],
        top_n=1,
    )
    assert calls == ["cuda", "cpu"]
    assert result[0]["rerank_score"] == 0.9


def test_reranker_singleton_includes_runtime_configuration():
    get_rerank_service.cache_clear()
    first = get_rerank_service(True, "fixture", "cpu", 4)
    second = get_rerank_service(True, "fixture", "cpu", 4)
    assert first is second


def test_qdrant_client_is_reused(monkeypatch):
    vector_service.close_qdrant_clients()

    class FakeQdrantClient:
        def __init__(self, **_kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(vector_service, "QdrantClient", FakeQdrantClient)
    monkeypatch.setattr(
        vector_service,
        "get_settings",
        lambda: SimpleNamespace(
            qdrant_collection="test",
            qdrant_url="http://127.0.0.1:6333",
            qdrant_api_key="",
            qdrant_prefer_grpc=False,
            qdrant_grpc_port=6334,
        ),
    )
    assert (
        VectorService(ensure_collection=False).client
        is VectorService(ensure_collection=False).client
    )


def test_safe_prewarm_does_not_call_llm_or_storage(monkeypatch):
    calls = []

    class Embedding:
        def embed_query(self, text):
            calls.append(("embedding", text))
            return [0.1]

    class Reranker:
        def prewarm(self):
            calls.append(("reranker", None))
            return True

    monkeypatch.setattr(
        rag_model_runtime, "get_embedding_service", lambda: Embedding()
    )
    monkeypatch.setattr(
        rag_model_runtime, "get_rerank_service", lambda: Reranker()
    )
    monkeypatch.setattr(
        rag_model_runtime,
        "embedding_runtime_status",
        lambda: {"status": "ready"},
    )
    rag_model_runtime._status["ready"] = False
    result = rag_model_runtime.prewarm_rag_models(
        Settings(app_env="test", model_prewarm_enabled=True)
    )
    assert result["status"] == "ready"
    assert calls == [
        ("embedding", "safe model readiness probe"),
        ("reranker", None),
    ]


def test_system_status_is_safe_and_degrades_when_qdrant_is_down(
    client, monkeypatch
):
    class DownQdrant:
        def get_collection(self, _name):
            raise ConnectionError("contains-secret")

    monkeypatch.setattr(
        system_status, "get_qdrant_client", lambda: DownQdrant()
    )
    response = client.get("/system/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["application"]["status"] == "degraded"
    assert payload["qdrant"]["status"] == "unavailable"
    assert payload["qdrant"]["error_code"] == "ConnectionError"
    rendered = str(payload).casefold()
    for forbidden in ("api_key", "password", "database_url", "contains-secret"):
        assert forbidden not in rendered


def test_manual_prewarm_endpoint_is_explicit_and_fail_open(
    client, monkeypatch
):
    captured = {}

    def fake_prewarm(_settings, *, force):
        captured["force"] = force
        return {"status": "error", "ready": False, "fail_open": True}

    from app.routers import system

    monkeypatch.setattr(system, "prewarm_rag_models", fake_prewarm)
    response = client.post("/system/models/prewarm")
    assert response.status_code == 200
    assert response.json()["fail_open"] is True
    assert captured["force"] is True


def test_conflict_answer_requires_current_context_bracket_citations():
    nodes = object.__new__(RagNodes)
    state = {
        "answer": "来源观点不同 [来源1]，另一来源持相反观点 [来源2]。",
        "answer_policy": "answer_with_uncertainty",
        "context_sources": [
            {"citation_index": 1, "source_id": "note-1"},
            {"citation_index": 2, "source_id": "note-2"},
        ],
        "retrieval_profile": {},
        "evaluation_result": {},
        "evidence_reason_codes": ["conflicting_evidence"],
        "performance_metrics": {},
    }
    validated = nodes.validate_citations(state)
    enforced = nodes.enforce_answer_safety({**state, **validated})
    assert enforced["citation_gate_passed"] is True
    assert {item["source_id"] for item in validated["citations"]} == {
        "note-1",
        "note-2",
    }


def test_conflict_citation_failure_never_fabricates_a_source():
    nodes = object.__new__(RagNodes)
    state = {
        "answer": "资料冲突，但没有规范引用。",
        "answer_policy": "answer_with_uncertainty",
        "context_sources": [
            {"citation_index": 1, "source_id": "note-1"},
            {"citation_index": 2, "source_id": "note-2"},
        ],
        "retrieval_profile": {},
        "evaluation_result": {},
        "evidence_reason_codes": ["conflicting_evidence"],
        "performance_metrics": {},
    }
    validated = nodes.validate_citations(state)
    enforced = nodes.enforce_answer_safety({**state, **validated})
    assert enforced["answer"] == CONFLICT_CITATION_FAILURE_ANSWER
    assert "[来源" not in enforced["answer"]
    assert enforced["answer_policy"] == "abstain"
    assert enforced["citations"] == []


def test_windows_scripts_keep_pid_and_cleanup_inside_project():
    root = Path(__file__).resolve().parents[1]
    start = (root / "scripts" / "start.ps1").read_text(encoding="utf-8")
    stop = (root / "scripts" / "stop.ps1").read_text(encoding="utf-8")
    cleanup = (root / "scripts" / "cleanup.ps1").read_text(
        encoding="utf-8"
    )
    assert "runtime\\pids\\fastapi.pid" in (
        root / "scripts" / "common.ps1"
    ).read_text(encoding="utf-8")
    assert "docker compose up -d qdrant" in start
    assert "docker compose up -d mysql" not in start.casefold()
    assert "Get-ProjectFastApiProcess" in stop
    assert "Stop-Process" in stop
    assert "[switch]$Apply" in cleanup
    assert "Vault" not in cleanup
    assert "qdrant" not in cleanup.casefold()
