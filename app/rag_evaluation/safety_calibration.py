from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from typing import Any

from app.rag_evaluation.schemas import EvaluationDataset


@dataclass(frozen=True, slots=True)
class SafetyObservation:
    case_id: str
    safety_split: str
    expected_policy: str
    features: dict[str, Any]


class SafetyCalibrationService:
    """Finite deterministic threshold search over calibration observations only."""

    THRESHOLD_KEYS = (
        "min_top_score",
        "min_mean_score",
        "min_context_chars",
        "min_valid_sources",
        "min_retrieval_agreement",
    )

    def observations(
        self,
        dataset: EvaluationDataset,
        case_rows: list[Any],
    ) -> list[SafetyObservation]:
        cases = {case.id: case for case in dataset.cases}
        result: list[SafetyObservation] = []
        seen: set[str] = set()
        for row in case_rows:
            case = cases.get(str(row.case_id))
            if (
                case is None
                or case.safety_split is None
                or case.expected_policy is None
                or row.status != "succeeded"
                or case.id in seen
            ):
                continue
            features = dict(
                (row.retrieval_stats or {}).get("evidence_features") or {}
            )
            if not features:
                continue
            seen.add(case.id)
            result.append(
                SafetyObservation(
                    case_id=case.id,
                    safety_split=case.safety_split,
                    expected_policy=case.expected_policy,
                    features=features,
                )
            )
        return result

    def calibrate(
        self,
        *,
        dataset_hash: str,
        observations: list[SafetyObservation],
    ) -> dict[str, Any]:
        calibration = [
            item
            for item in observations
            if item.safety_split == "calibration"
        ]
        if not calibration:
            raise ValueError("Calibration observations are required")
        candidate_values = self._candidate_values(calibration)
        best: (
            tuple[tuple[float, ...], dict[str, float], dict[str, float]]
            | None
        ) = None
        for values in itertools.product(
            *(candidate_values[key] for key in self.THRESHOLD_KEYS)
        ):
            thresholds = dict(
                zip(self.THRESHOLD_KEYS, values, strict=True)
            )
            metrics = self.evaluate(calibration, thresholds)
            eligible = self.passes_core_gates(metrics)
            rank = (
                float(eligible),
                metrics["answerability_accuracy"],
                metrics["policy_accuracy"],
                metrics["no_answer_accuracy"],
                -metrics["false_refusal_rate"],
                -metrics["false_answer_rate"],
            )
            if best is None or rank > best[0]:
                best = (rank, thresholds, metrics)
        assert best is not None
        _, thresholds, metrics = best
        return {
            "thresholds": thresholds,
            "metrics": metrics,
            "dataset_hash": dataset_hash,
            "configuration_hash": self.configuration_hash(
                dataset_hash=dataset_hash,
                thresholds=thresholds,
            ),
            "calibration_case_count": len(calibration),
            "holdout_used_for_tuning": False,
            "eligible": self.passes_core_gates(metrics),
        }

    def validate_holdout(
        self,
        *,
        observations: list[SafetyObservation],
        thresholds: dict[str, float],
    ) -> dict[str, Any]:
        holdout = [
            item for item in observations if item.safety_split == "holdout"
        ]
        if not holdout:
            raise ValueError("Holdout observations are required")
        metrics = self.evaluate(holdout, thresholds)
        return {
            "case_count": len(holdout),
            "metrics": metrics,
            "passed": self.passes_core_gates(metrics),
            "all_abstain": metrics["answer_rate"] == 0,
        }

    def evaluate(
        self,
        observations: list[SafetyObservation],
        thresholds: dict[str, float],
    ) -> dict[str, float]:
        if not observations:
            raise ValueError("Safety observations are required")
        policy_correct = answerability_correct = 0
        no_answer_total = no_answer_correct = false_answers = 0
        answer_total = false_refusals = answers = 0
        for item in observations:
            actual = self.predict(item.features, thresholds)
            expected = item.expected_policy
            policy_correct += actual == expected
            expected_answerable = expected != "abstain"
            actual_answerable = actual != "abstain"
            answerability_correct += expected_answerable == actual_answerable
            answers += actual_answerable
            if expected == "abstain":
                no_answer_total += 1
                no_answer_correct += not actual_answerable
                false_answers += actual_answerable
            else:
                answer_total += 1
                false_refusals += not actual_answerable
        total = len(observations)
        return {
            "policy_accuracy": policy_correct / total,
            "answerability_accuracy": answerability_correct / total,
            "no_answer_accuracy": (
                no_answer_correct / no_answer_total
                if no_answer_total
                else 1.0
            ),
            "false_answer_rate": (
                false_answers / no_answer_total if no_answer_total else 0.0
            ),
            "false_refusal_rate": (
                false_refusals / answer_total if answer_total else 0.0
            ),
            "answer_rate": answers / total,
        }

    @staticmethod
    def passes_core_gates(metrics: dict[str, float]) -> bool:
        return (
            metrics["no_answer_accuracy"] >= 0.75
            and metrics["false_answer_rate"] <= 0.25
            and metrics["answerability_accuracy"] >= 0.85
            and metrics["false_refusal_rate"] <= 0.10
            and metrics["answer_rate"] > 0
        )

    @staticmethod
    def predict(
        features: dict[str, Any],
        thresholds: dict[str, float],
    ) -> str:
        blocking = (
            int(features.get("candidate_count") or 0) == 0
            or int(features.get("reranked_count") or 0) == 0
            or int(features.get("context_chunk_count") or 0) == 0
            or float(features.get("context_char_count") or 0)
            < thresholds["min_context_chars"]
            or float(features.get("valid_source_count") or 0)
            < thresholds["min_valid_sources"]
            or not bool(features.get("source_scope_match", True))
        )
        top = features.get("top_score")
        score_mean = features.get("top_k_score_mean")
        weak_scores = (
            top is None
            or score_mean is None
            or (
                float(top) < thresholds["min_top_score"]
                and float(score_mean) < thresholds["min_mean_score"]
            )
        )
        low_agreement = (
            bool(features.get("rewritten_retrieval_hit"))
            and float(features.get("retrieval_agreement") or 0)
            < thresholds["min_retrieval_agreement"]
        )
        if blocking or weak_scores or low_agreement:
            return "abstain"
        if (
            bool(features.get("conflict_intent"))
            and int(features.get("distinct_note_count") or 0) >= 2
        ):
            return "answer_with_uncertainty"
        return "answer"

    def _candidate_values(
        self, observations: list[SafetyObservation]
    ) -> dict[str, list[float]]:
        feature_keys = {
            "min_top_score": "top_score",
            "min_mean_score": "top_k_score_mean",
            "min_context_chars": "context_char_count",
            "min_valid_sources": "valid_source_count",
            "min_retrieval_agreement": "retrieval_agreement",
        }
        defaults = {
            "min_top_score": -10.0,
            "min_mean_score": -10.0,
            "min_context_chars": 80.0,
            "min_valid_sources": 1.0,
            "min_retrieval_agreement": 0.0,
        }
        result: dict[str, list[float]] = {}
        for threshold_key, feature_key in feature_keys.items():
            values = sorted(
                {
                    float(value)
                    for item in observations
                    if isinstance(
                        (value := item.features.get(feature_key)),
                        (int, float),
                    )
                    and math.isfinite(float(value))
                }
            )
            if not values:
                result[threshold_key] = [defaults[threshold_key]]
                continue
            indexes = {
                0,
                len(values) // 4,
                len(values) // 2,
                (len(values) * 3) // 4,
                len(values) - 1,
            }
            selected = {values[index] for index in indexes}
            selected.add(values[0] - 1e-9)
            result[threshold_key] = sorted(selected)
        return result

    @staticmethod
    def configuration_hash(
        *, dataset_hash: str, thresholds: dict[str, float]
    ) -> str:
        payload = json.dumps(
            {
                "dataset_hash": dataset_hash,
                "feature_version": "evidence-v1",
                "thresholds": thresholds,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
