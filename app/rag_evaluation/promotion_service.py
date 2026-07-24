from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    eligible: bool
    primary_gain_passed: bool
    safeguards_passed: bool
    reasons: tuple[str, ...]
    rollback_profile_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "primary_gain_passed": self.primary_gain_passed,
            "safeguards_passed": self.safeguards_passed,
            "reasons": list(self.reasons),
            "rollback_profile_version": self.rollback_profile_version,
        }


class PromotionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def decide(
        self,
        *,
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        baseline_profile_version: str = "rag-baseline-v1",
    ) -> PromotionDecision:
        reasons: list[str] = []
        recall_gain = self._delta(candidate, baseline, "recall@10")
        ndcg_gain = self._delta(candidate, baseline, "ndcg@10")
        primary = (
            recall_gain >= self.settings.rag_promotion_min_recall_gain
            or ndcg_gain >= self.settings.rag_promotion_min_ndcg_gain
        )
        if not primary:
            reasons.append("primary_recall_or_ndcg_gain_below_threshold")

        citation_drop = self._delta(baseline, candidate, "citation_precision")
        no_answer_drop = self._delta(baseline, candidate, "no_answer_accuracy")
        baseline_p95 = self._number(baseline.get("p95_latency"))
        candidate_p95 = self._number(candidate.get("p95_latency"))
        latency_ratio = (
            (candidate_p95 - baseline_p95) / baseline_p95
            if baseline_p95 > 0
            else (0.0 if candidate_p95 <= 0 else float("inf"))
        )
        safeguards = True
        if citation_drop > self.settings.rag_promotion_max_citation_drop:
            safeguards = False
            reasons.append("citation_precision_drop_exceeds_threshold")
        if no_answer_drop > self.settings.rag_promotion_max_no_answer_drop:
            safeguards = False
            reasons.append("no_answer_accuracy_drop_exceeds_threshold")
        if self._number(candidate.get("false_answer_rate")) > self._number(
            baseline.get("false_answer_rate")
        ):
            safeguards = False
            reasons.append("false_answer_rate_increased")
        if latency_ratio > self.settings.rag_promotion_max_p95_latency_increase_ratio:
            safeguards = False
            reasons.append("p95_latency_increase_exceeds_threshold")
        if self._number(candidate.get("model_call_count")) > self._number(
            baseline.get("model_call_count")
        ) + 1e-9:
            safeguards = False
            reasons.append("model_call_count_increased")
        return PromotionDecision(
            eligible=primary and safeguards,
            primary_gain_passed=primary,
            safeguards_passed=safeguards,
            reasons=tuple(reasons or ["all_promotion_rules_passed"]),
            rollback_profile_version=baseline_profile_version,
        )

    @staticmethod
    def _number(value: Any) -> float:
        return float(value) if isinstance(value, (int, float)) else 0.0

    @classmethod
    def _delta(cls, left: dict[str, Any], right: dict[str, Any], key: str) -> float:
        return cls._number(left.get(key)) - cls._number(right.get(key))
