from __future__ import annotations

from typing import Any


class ComparisonService:
    @staticmethod
    def compare(
        experiments: list[dict[str, Any]],
        *,
        baseline_index: int = 0,
    ) -> dict[str, Any]:
        if not 2 <= len(experiments) <= 6:
            raise ValueError("Compare between 2 and 6 experiments")
        baseline = experiments[baseline_index]
        baseline_metrics = baseline.get("metric_summary") or {}
        rows: list[dict[str, Any]] = []
        for experiment in experiments:
            metrics = experiment.get("metric_summary") or {}
            deltas: dict[str, float] = {}
            for key, value in metrics.items():
                baseline_value = baseline_metrics.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and isinstance(baseline_value, (int, float))
                    and not isinstance(baseline_value, bool)
                ):
                    deltas[key] = float(value) - float(baseline_value)
            rows.append(
                {
                    "experiment_id": experiment.get("id"),
                    "name": experiment.get("name"),
                    "profile_version": experiment.get("retrieval_profile_version"),
                    "metrics": metrics,
                    "delta_from_baseline": deltas,
                }
            )
        return {
            "baseline_experiment_id": baseline.get("id"),
            "experiments": rows,
        }
