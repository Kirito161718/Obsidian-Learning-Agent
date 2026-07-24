from __future__ import annotations

import time
from typing import Any

from app.core.config import Settings, get_settings
from app.graphs.rag_graph import RagWorkflow
from app.rag_evaluation.metrics import relevance_grade
from app.rag_evaluation.schemas import (
    EvaluationCase,
    EvaluationRunOutput,
)
from app.retrieval.configuration import RetrievalProfileConfiguration
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import DeepSeekClient
from app.services.rerank_service import RerankService, get_rerank_service
from app.services.vector_service import VectorService


class ProductionRagEvaluationAdapter:
    """Runs the existing graph in non-persistent evaluation mode."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embedding_service = None
        self._llm_client = None
        self._workflows: dict[str, RagWorkflow] = {}
        self._rerank_services: dict[tuple[bool, str], RerankService] = {}
        self._invocation_count = 0

    def __call__(
        self,
        case: EvaluationCase,
        profile: RetrievalProfileConfiguration,
        mode: str,
    ) -> EvaluationRunOutput:
        started = time.perf_counter()
        cold_start = self._invocation_count == 0
        self._invocation_count += 1
        embedding_started = time.perf_counter()
        embedding_cold = self._embedding_service is None
        embedding_service = self._embedding_service or get_embedding_service()
        embedding_initialization_ms = (
            (time.perf_counter() - embedding_started) * 1000
            if embedding_cold
            else 0.0
        )
        self._embedding_service = embedding_service
        llm_client = self._llm_client or DeepSeekClient()
        self._llm_client = llm_client
        profile_hash = profile.content_hash()
        workflow = self._workflows.get(profile_hash)
        if workflow is None:
            workflow_settings = self.settings.model_copy(
                update={
                    "rag_context_max_chars": profile.rag_context_max_chars,
                    "rag_max_chunks_per_source": profile.max_chunks_per_note,
                }
            )
            vector_service = VectorService(
                ensure_collection=False,
                collection_name=profile.collection_name,
            )
            rerank_key = (profile.rerank_enabled, profile.rerank_model)
            rerank_service = self._rerank_services.get(rerank_key)
            if rerank_service is None:
                rerank_service = get_rerank_service(
                    enabled=profile.rerank_enabled,
                    model_name=profile.rerank_model,
                )
                self._rerank_services[rerank_key] = rerank_service
            workflow = RagWorkflow(
                settings=workflow_settings,
                embedding_service=embedding_service,
                vector_service=vector_service,
                rerank_service=rerank_service,
                llm_client=llm_client,
            )
            self._workflows[profile_hash] = workflow
        source_paths = None
        state = workflow.invoke(
            mode="ask" if mode == "full" else "retrieve",
            question=case.question,
            document_id=None,
            top_k=profile.final_top_k,
            source_types=["obsidian_note"],
            source_path_prefixes=source_paths,
            session_id=None,
            run_id=None,
            db=None,
            evaluation_mode=True,
            evaluation_conversation_context=[
                turn.model_dump() for turn in case.conversation_context
            ],
            retrieval_profile=profile.canonical_dict(),
        )
        original_items = [
            chunk.to_rerank_item() for chunk in state.get("original_results", [])
        ]
        rewritten_items = [
            chunk.to_rerank_item() for chunk in state.get("rewritten_results", [])
        ]
        pre_rerank = [
            chunk.to_rerank_item() for chunk in state.get("expanded_results", [])
        ]
        retrieved = [
            chunk.to_rerank_item() for chunk in state.get("context_chunks", [])
        ]
        citations = [
            {
                "source_id": item.get("source_id"),
                "heading_path": item.get("heading_path") or [],
            }
            for item in state.get("citations", [])
        ]
        original_hit = any(
            relevance_grade(item, case.relevance_judgments) > 0
            for item in original_items
        )
        rewritten_hit = (
            any(
                relevance_grade(item, case.relevance_judgments) > 0
                for item in rewritten_items
            )
            if state.get("rewrite_applied")
            else None
        )
        total_ms = (time.perf_counter() - started) * 1000
        evaluation = state.get("evaluation_result") or {}
        manifest = state.get("context_manifest") or {}
        performance = dict(state.get("performance_metrics") or {})
        state["cold_start"] = cold_start
        performance["cold_start"] = cold_start
        performance["embedding_initialization_ms"] = (
            embedding_initialization_ms
        )
        performance["total_ms"] = total_ms
        return EvaluationRunOutput(
            retrieved=retrieved,
            pre_rerank=pre_rerank,
            answer=state.get("answer") if mode == "full" else None,
            citations=citations,
            rewrite_triggered=bool(state.get("rewrite_applied")),
            original_query_hit=original_hit,
            rewritten_query_hit=rewritten_hit,
            answerable=(
                bool(state.get("answerable")) if mode == "full" else bool(retrieved)
            ),
            model_call_count=int(state.get("model_call_count") or 0),
            rewrite_call_count=int(bool(state.get("rewrite_applied"))),
            retrieval_latency_ms=float(state.get("retrieval_latency_ms") or 0),
            rewrite_latency_ms=float(state.get("rewrite_latency_ms") or 0),
            rerank_latency_ms=float(state.get("rerank_latency_ms") or 0),
            llm_latency_ms=float(state.get("llm_latency_ms") or 0),
            total_latency_ms=total_ms,
            metadata={
                "rewrite_reason": state.get("rewrite_reason"),
                "context_truncated": bool(
                    (manifest.get("knowledge_context") or {}).get("truncated")
                ),
                "citation_validation_passed": not bool(
                    evaluation.get("invalid_citations")
                ),
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
                "evidence_features": dict(
                    state.get("evidence_features") or {}
                ),
                "citation_gate_passed": state.get("citation_gate_passed"),
                "abstained_before_llm": bool(
                    state.get("abstained_before_llm")
                ),
                "uncertainty_answer": bool(
                    state.get("uncertainty_answer")
                ),
                "cold_start": bool(state.get("cold_start")),
                "cache_hit": bool(state.get("cache_hit")),
                "performance_metrics": performance,
            },
        )
