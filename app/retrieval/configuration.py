from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings, get_settings


class RetrievalProfileConfiguration(BaseModel):
    """Versioned, serializable controls for one isolated retrieval experiment."""

    model_config = ConfigDict(extra="forbid")

    chunking_version: str = "markdown-structural-v1"
    retrieval_mode: Literal["dense", "hybrid"] = "dense"
    collection_name: str = "rag_chunks"
    original_top_k: int = Field(default=25, ge=1, le=500)
    rewritten_top_k: int = Field(default=25, ge=1, le=500)
    final_top_k: int = Field(default=5, ge=1, le=100)
    rewrite_enabled: bool = True
    dual_retrieval_enabled: bool = True
    session_memory_for_rewrite: bool = True
    rerank_enabled: bool = False
    rerank_top_k: int = Field(default=5, ge=1, le=100)
    max_chunks_per_note: int = Field(default=3, ge=1, le=100)
    link_expansion_enabled: bool = False
    link_expansion_limit: int = Field(default=2, ge=0, le=20)
    neighbor_expansion_enabled: bool = False
    neighbor_window: int = Field(default=1, ge=0, le=3)
    mmr_enabled: bool = False
    mmr_lambda: float = Field(default=0.7, ge=0, le=1)
    parent_child_enabled: bool = False
    hybrid_enabled: bool = False
    fusion_method: Literal["rrf", "dbsf"] = "rrf"
    rag_context_max_chars: int = Field(default=12000, ge=1000, le=100_000)
    no_answer_score_threshold: float = Field(default=0.45, ge=-1, le=1)
    embedding_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    prompt_version: str = "rag-system-v1"

    @model_validator(mode="after")
    def validate_compatible_controls(self) -> "RetrievalProfileConfiguration":
        if self.hybrid_enabled and self.retrieval_mode != "hybrid":
            raise ValueError("hybrid_enabled requires retrieval_mode='hybrid'")
        if self.retrieval_mode == "hybrid" and not self.hybrid_enabled:
            raise ValueError("retrieval_mode='hybrid' requires hybrid_enabled")
        if self.rerank_top_k < self.final_top_k and self.rerank_enabled:
            raise ValueError("rerank_top_k cannot be smaller than final_top_k")
        return self

    @classmethod
    def baseline(cls, settings: Settings | None = None) -> "RetrievalProfileConfiguration":
        settings = settings or get_settings()
        final_top_k = min(20, max(1, settings.rerank_top_n))
        return cls(
            collection_name=settings.qdrant_collection,
            original_top_k=max(final_top_k * 5, 20),
            rewritten_top_k=max(final_top_k * 5, 20),
            final_top_k=final_top_k,
            rewrite_enabled=settings.rag_rewrite_enabled,
            dual_retrieval_enabled=True,
            rerank_enabled=settings.rerank_enabled,
            rerank_top_k=max(final_top_k, settings.rerank_top_n),
            max_chunks_per_note=settings.rag_max_chunks_per_source,
            link_expansion_enabled=False,
            rag_context_max_chars=settings.rag_context_max_chars,
            no_answer_score_threshold=settings.rag_rewrite_score_threshold,
            embedding_model=settings.embedding_model_name,
            rerank_model=settings.rerank_model,
        )

    def canonical_dict(self) -> dict:
        return self.model_dump(mode="json")

    def content_hash(self) -> str:
        payload = json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ablation_configurations(
    baseline: RetrievalProfileConfiguration,
) -> dict[str, RetrievalProfileConfiguration]:
    return {
        "baseline": baseline,
        "no-rewrite": baseline.model_copy(
            update={"rewrite_enabled": False, "dual_retrieval_enabled": False}
        ),
        "original-only": baseline.model_copy(
            update={"dual_retrieval_enabled": False}
        ),
        "no-rerank": baseline.model_copy(update={"rerank_enabled": False}),
        "no-link-expansion": baseline.model_copy(
            update={"link_expansion_enabled": False}
        ),
        "no-memory-rewrite": baseline.model_copy(
            update={"session_memory_for_rewrite": False}
        ),
    }


def candidate_configurations(
    baseline: RetrievalProfileConfiguration,
    *,
    shadow_collection: str,
) -> dict[str, RetrievalProfileConfiguration]:
    return {
        "chunk-v2": baseline.model_copy(
            update={
                "chunking_version": "markdown-structural-v2",
                "collection_name": shadow_collection,
            }
        ),
        "link-v2": baseline.model_copy(
            update={"link_expansion_enabled": True, "link_expansion_limit": 2}
        ),
        "neighbor": baseline.model_copy(
            update={"neighbor_expansion_enabled": True, "neighbor_window": 1}
        ),
        "chunk-v2-link": baseline.model_copy(
            update={
                "chunking_version": "markdown-structural-v2",
                "collection_name": shadow_collection,
                "link_expansion_enabled": True,
                "link_expansion_limit": 2,
            }
        ),
        "chunk-v2-no-rerank": baseline.model_copy(
            update={
                "chunking_version": "markdown-structural-v2",
                "collection_name": shadow_collection,
                "rerank_enabled": False,
            }
        ),
    }
