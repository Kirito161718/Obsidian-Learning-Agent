from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.rag_evaluation.schemas import CaseEvaluationResult


class ReportBuilder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(
        self,
        *,
        experiment_id: str,
        experiment_name: str,
        dataset_version: str,
        profile_version: str,
        configuration_hash: str,
        metric_summary: dict[str, Any],
        failure_summary: dict[str, Any],
        results: list[CaseEvaluationResult],
    ) -> Path:
        root = self.settings.rag_evaluation_report_dir / experiment_id
        root.mkdir(parents=True, exist_ok=True)
        safe_summary = {
            "experiment_id": experiment_id,
            "experiment_name": experiment_name,
            "dataset_version": dataset_version,
            "profile_version": profile_version,
            "configuration_hash": configuration_hash,
            "metric_summary": metric_summary,
            "case_runs": len(results),
        }
        (root / "summary.json").write_text(
            json.dumps(safe_summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (root / "failure_analysis.json").write_text(
            json.dumps(failure_summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        metric_keys = sorted({key for result in results for key in result.metrics})
        with (root / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["case_id", "category", "repeat_index", "status", *metric_keys],
            )
            writer.writeheader()
            for result in results:
                writer.writerow(
                    {
                        "case_id": result.case_id,
                        "category": result.category,
                        "repeat_index": result.repeat_index,
                        "status": result.status,
                        **result.metrics,
                    }
                )
        lines = [
            f"# RAG Evaluation: {experiment_name}",
            "",
            f"- Dataset version: `{dataset_version}`",
            f"- Retrieval profile: `{profile_version}`",
            f"- Configuration hash: `{configuration_hash}`",
            f"- Case runs: {len(results)}",
            "",
            "## Metric summary",
            "",
        ]
        lines.extend(
            f"- `{key}`: {value}"
            for key, value in sorted(metric_summary.items())
            if key != "category_metrics"
        )
        category_metrics = metric_summary.get("category_metrics") or {}
        if category_metrics:
            lines.extend(
                [
                    "",
                    "## Category metrics",
                    "",
                    "| Category | Recall@10 | NDCG@10 | Hit@10 | p95 latency |",
                    "| --- | ---: | ---: | ---: | ---: |",
                ]
            )
            for category, values in sorted(category_metrics.items()):
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(category),
                            str(values.get("recall@10", "unavailable")),
                            str(values.get("ndcg@10", "unavailable")),
                            str(values.get("hit_rate@10", "unavailable")),
                            str(values.get("p95_latency", "unavailable")),
                        ]
                    )
                    + " |"
                )
        lines.extend(["", "## Failure summary", ""])
        counts = failure_summary.get("counts") or {}
        lines.extend(f"- `{key}`: {value}" for key, value in sorted(counts.items()))
        lines.extend(
            [
                "",
                "This report intentionally omits full questions, answers, prompts, "
                "contexts, note bodies, secrets, and absolute paths.",
                "",
            ]
        )
        (root / "report.md").write_text("\n".join(lines), encoding="utf-8")
        return root
