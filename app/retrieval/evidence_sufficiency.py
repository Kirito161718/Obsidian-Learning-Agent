from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Literal

from app.domain.retrieved_chunk import RetrievedChunk

AnswerPolicy = Literal["answer", "abstain", "answer_with_uncertainty"]

CONFLICT_INTENT_TERMS = (
    "冲突",
    "矛盾",
    "不一致",
    "差异",
    "无法确定",
    "不确定",
    "conflict",
    "contradict",
    "uncertain",
)


@dataclass(frozen=True, slots=True)
class EvidenceFeatures:
    candidate_count: int
    reranked_count: int
    distinct_note_count: int
    top_score: float | None
    second_score: float | None
    score_margin: float | None
    top_k_score_mean: float | None
    original_retrieval_hit: bool
    rewritten_retrieval_hit: bool
    retrieval_agreement: float
    context_chunk_count: int
    context_char_count: int
    valid_source_count: int
    source_scope_match: bool
    conflict_intent: bool
    score_kind: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    policy: AnswerPolicy
    confidence_band: Literal["low", "medium", "high"]
    reason_codes: tuple[str, ...]
    calibration_version: str
    feature_values: EvidenceFeatures

    def public_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "confidence_band": self.confidence_band,
            "reason_codes": list(self.reason_codes),
            "calibration_version": self.calibration_version,
            "feature_values": self.feature_values.public_dict(),
        }


class EvidenceSufficiencyService:
    """Deterministic evidence gate; it never reads generated answer text."""

    DEFAULT_THRESHOLDS = {
        "min_top_score": -10.0,
        "min_mean_score": -10.0,
        "min_context_chars": 80.0,
        "min_valid_sources": 1.0,
        "min_retrieval_agreement": 0.0,
    }

    def evaluate(
        self,
        *,
        original_results: list[RetrievedChunk],
        rewritten_results: list[RetrievedChunk],
        candidates: list[RetrievedChunk],
        reranked: list[RetrievedChunk],
        context_chunks: list[RetrievedChunk],
        context_text: str,
        context_sources: list[dict[str, Any]],
        source_types: list[str] | None,
        source_path_prefixes: list[str] | None,
        question: str,
        thresholds: dict[str, float] | None,
        calibration_version: str,
    ) -> EvidenceDecision:
        limits = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        features = self.features(
            original_results=original_results,
            rewritten_results=rewritten_results,
            candidates=candidates,
            reranked=reranked,
            context_chunks=context_chunks,
            context_text=context_text,
            context_sources=context_sources,
            source_types=source_types,
            source_path_prefixes=source_path_prefixes,
            question=question,
        )
        reasons: list[str] = []
        if features.candidate_count == 0 or features.reranked_count == 0:
            reasons.append("no_candidates")
        if (
            features.context_chunk_count == 0
            or features.context_char_count < limits["min_context_chars"]
        ):
            reasons.append("insufficient_context")
        if features.valid_source_count < limits["min_valid_sources"]:
            reasons.append("citation_support_missing")
        if not features.source_scope_match:
            reasons.append("scope_mismatch")

        weak_top = (
            features.top_score is None
            or features.top_score < limits["min_top_score"]
        )
        weak_mean = (
            features.top_k_score_mean is None
            or features.top_k_score_mean < limits["min_mean_score"]
        )
        if weak_top:
            reasons.append("weak_top_score")
        if weak_mean:
            reasons.append("weak_score_mean")
        if (
            rewritten_results
            and features.retrieval_agreement
            < limits["min_retrieval_agreement"]
        ):
            reasons.append("low_retrieval_agreement")

        blocking = {
            "no_candidates",
            "insufficient_context",
            "citation_support_missing",
            "scope_mismatch",
            "low_retrieval_agreement",
        }
        if blocking.intersection(reasons) or (weak_top and weak_mean):
            return EvidenceDecision(
                policy="abstain",
                confidence_band="low",
                reason_codes=tuple(dict.fromkeys(reasons)),
                calibration_version=calibration_version,
                feature_values=features,
            )
        if features.conflict_intent and features.distinct_note_count >= 2:
            return EvidenceDecision(
                policy="answer_with_uncertainty",
                confidence_band="medium",
                reason_codes=("conflicting_evidence",),
                calibration_version=calibration_version,
                feature_values=features,
            )
        return EvidenceDecision(
            policy="answer",
            confidence_band="high",
            reason_codes=("sufficient_evidence",),
            calibration_version=calibration_version,
            feature_values=features,
        )

    def features(
        self,
        *,
        original_results: list[RetrievedChunk],
        rewritten_results: list[RetrievedChunk],
        candidates: list[RetrievedChunk],
        reranked: list[RetrievedChunk],
        context_chunks: list[RetrievedChunk],
        context_text: str,
        context_sources: list[dict[str, Any]],
        source_types: list[str] | None,
        source_path_prefixes: list[str] | None,
        question: str,
    ) -> EvidenceFeatures:
        use_rerank = any(
            item.rerank_score is not None and math.isfinite(item.rerank_score)
            for item in context_chunks
        )
        scores = [
            float(item.rerank_score if use_rerank else item.score)
            for item in context_chunks
            if (item.rerank_score if use_rerank else item.score) is not None
            and math.isfinite(float(item.rerank_score if use_rerank else item.score))
        ]
        scores.sort(reverse=True)
        original_ids = {item.chunk_id for item in original_results}
        rewritten_ids = {item.chunk_id for item in rewritten_results}
        union = original_ids | rewritten_ids
        agreement = (
            len(original_ids & rewritten_ids) / len(union)
            if rewritten_ids and union
            else 1.0
        )
        allowed_types = set(source_types or [])
        prefixes = tuple(
            value.replace("\\", "/").strip("/")
            for value in (source_path_prefixes or [])
        )
        scope_match = all(
            (not allowed_types or item.source_type in allowed_types)
            and (
                not prefixes
                or any(
                    (item.source_path or "").replace("\\", "/").startswith(prefix)
                    for prefix in prefixes
                )
            )
            for item in context_chunks
        )
        return EvidenceFeatures(
            candidate_count=len(candidates),
            reranked_count=len(reranked),
            distinct_note_count=len(
                {item.source_id for item in context_chunks if item.source_id}
            ),
            top_score=scores[0] if scores else None,
            second_score=scores[1] if len(scores) > 1 else None,
            score_margin=(
                scores[0] - scores[1] if len(scores) > 1 else None
            ),
            top_k_score_mean=mean(scores) if scores else None,
            original_retrieval_hit=bool(original_results),
            rewritten_retrieval_hit=bool(rewritten_results),
            retrieval_agreement=agreement,
            context_chunk_count=len(context_chunks),
            context_char_count=len(context_text),
            valid_source_count=len(
                {
                    str(source.get("source_id") or "")
                    for source in context_sources
                    if source.get("source_id")
                }
            ),
            source_scope_match=scope_match,
            conflict_intent=any(
                term.casefold() in question.casefold()
                for term in CONFLICT_INTENT_TERMS
            ),
            score_kind="rerank_score" if use_rerank else "vector_score",
        )
