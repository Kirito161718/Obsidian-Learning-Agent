from __future__ import annotations

import random
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import RagEvalDataset, RagEvalExperiment, RagRetrievalProfile
from app.rag_evaluation.evaluators import evaluate_output
from app.rag_evaluation.experiment_service import (
    ExperimentService,
    LangSmithExperimentPublisher,
)
from app.rag_evaluation.failure_analysis import classify_failures, summarize_failures
from app.rag_evaluation.metrics import (
    aggregate_metric_rows,
    aggregate_repeat_metrics,
    retrieval_identity,
)
from app.rag_evaluation.report_builder import ReportBuilder
from app.rag_evaluation.schemas import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunOutput,
)
from app.retrieval.configuration import RetrievalProfileConfiguration

CaseRunner = Callable[
    [EvaluationCase, RetrievalProfileConfiguration, str], EvaluationRunOutput
]


class EvaluationRunner:
    def __init__(
        self,
        case_runner: CaseRunner,
        *,
        settings: Settings | None = None,
        experiment_service: ExperimentService | None = None,
        publisher: LangSmithExperimentPublisher | None = None,
        report_builder: ReportBuilder | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.case_runner = case_runner
        self.experiments = experiment_service or ExperimentService(self.settings)
        self.publisher = publisher or LangSmithExperimentPublisher(self.settings)
        self.reports = report_builder or ReportBuilder(self.settings)

    def run(
        self,
        db: Session,
        *,
        dataset_record: RagEvalDataset,
        dataset: EvaluationDataset,
        profile: RagRetrievalProfile,
        experiment: RagEvalExperiment,
        mode: str = "retrieval",
        categories: set[str] | None = None,
        case_ids: set[str] | None = None,
        repeat_count: int | None = None,
        seed: int = 7,
        publish_langsmith: bool = True,
    ) -> list[CaseEvaluationResult]:
        if mode not in {"retrieval", "full"}:
            raise ValueError("mode must be retrieval or full")
        if experiment.experiment_type != mode:
            raise ValueError(
                "Runner mode must match the persisted experiment type"
            )
        configuration = RetrievalProfileConfiguration.model_validate(
            profile.configuration
        )
        cases = [
            case
            for case in dataset.cases
            if case.review_status != "stale"
            and (not categories or case.category in categories)
            and (not case_ids or case.id in case_ids)
        ][: self.settings.rag_evaluation_max_cases]
        repeats = repeat_count or self.settings.rag_evaluation_repeat_count
        if not 1 <= repeats <= 20:
            raise ValueError("repeat_count must be between 1 and 20")
        random.seed(seed)
        persisted = self.experiments.case_results(db, experiment.id)
        completed_keys = {
            (row.case_id, row.repeat_index)
            for row in persisted
            if row.status == "succeeded"
        }
        requested_keys = {
            (case.id, repeat_index)
            for repeat_index in range(repeats)
            for case in cases
        }
        if experiment.status == "pending":
            self.experiments.mark_running(db, experiment)
        elif experiment.status in {"failed", "completed"}:
            self.experiments.resume(
                db,
                experiment,
                allow_completed_extension=(
                    experiment.status == "completed"
                    and not requested_keys.issubset(completed_keys)
                ),
            )
        elif experiment.status == "cancel_requested":
            pass
        elif experiment.status != "running":
            raise ValueError(
                "Only pending, running, failed, or cancellation-requested "
                "experiments can be executed"
            )
        results: list[CaseEvaluationResult] = [
            CaseEvaluationResult(
                case_id=row.case_id,
                category=row.category,
                repeat_index=row.repeat_index,
                status=row.status,
                metrics=dict(row.metric_values or {}),
                retrieval_stats=dict(row.retrieval_stats or {}),
                answer_stats=dict(row.answer_stats or {}),
                failure_types=list(
                    (row.retrieval_stats or {}).get("failure_types")
                    or self._infer_persisted_failures(row.metric_values or {})
                ),
                internal_run_id=row.internal_run_id,
                external_trace_id=row.external_trace_id,
                error_code=row.error_code,
            )
            for row in persisted
            if row.status == "succeeded"
        ]
        try:
            for repeat_index in range(repeats):
                for case in cases:
                    if (case.id, repeat_index) in completed_keys:
                        continue
                    if self.experiments.cancellation_requested(db, experiment):
                        self.experiments.mark_cancelled(db, experiment)
                        return results
                    started = time.perf_counter()
                    output: EvaluationRunOutput | None = None
                    try:
                        output = self.case_runner(case, configuration, mode)
                        if output.total_latency_ms <= 0:
                            output.total_latency_ms = (
                                time.perf_counter() - started
                            ) * 1000
                        metrics = evaluate_output(
                            case,
                            output,
                            k_values=self.settings.rag_evaluation_top_k_values,
                            include_answer_metrics=mode == "full",
                        )
                        failures = classify_failures(
                            case,
                            output,
                            metrics,
                            max_chunks_per_note=configuration.max_chunks_per_note,
                        )
                        note_ids = [
                            retrieval_identity(item)[0]
                            for item in output.retrieved
                            if retrieval_identity(item)[0]
                        ]
                        result = CaseEvaluationResult(
                            case_id=case.id,
                            category=case.category,
                            repeat_index=repeat_index,
                            status="succeeded",
                            metrics=metrics,
                            retrieval_stats={
                                "candidate_count": len(output.retrieved),
                                "pre_rerank_count": len(output.pre_rerank),
                                "note_count": len(set(note_ids)),
                                "duplicate_count": len(note_ids) - len(set(note_ids)),
                                "evidence_features": dict(
                                    output.metadata.get("evidence_features")
                                    or {}
                                ),
                                "evidence_reason_codes": list(
                                    output.metadata.get(
                                        "evidence_reason_codes"
                                    )
                                    or []
                                ),
                                "performance_metrics": dict(
                                    output.metadata.get(
                                        "performance_metrics"
                                    )
                                    or {}
                                ),
                            },
                            answer_stats={
                                "predicted_answerable": output.answerable,
                                "citation_count": len(output.citations),
                                "answer_char_count": len(output.answer or ""),
                                "answer_policy": output.metadata.get(
                                    "answer_policy"
                                ),
                                "citation_gate_passed": output.metadata.get(
                                    "citation_gate_passed"
                                ),
                                "abstained_before_llm": bool(
                                    output.metadata.get(
                                        "abstained_before_llm"
                                    )
                                ),
                                "uncertainty_answer": bool(
                                    output.metadata.get(
                                        "uncertainty_answer"
                                    )
                                ),
                            },
                            failure_types=failures,
                            internal_run_id=output.internal_run_id,
                            external_trace_id=output.external_trace_id,
                        )
                    except Exception as exc:
                        result = CaseEvaluationResult(
                            case_id=case.id,
                            category=case.category,
                            repeat_index=repeat_index,
                            status="failed",
                            error_code=type(exc).__name__,
                            failure_types=["case_execution_failed"],
                        )
                    results.append(result)
                    self.experiments.add_case_result(
                        db,
                        experiment_id=experiment.id,
                        result=result,
                        latency_ms=(
                            round(output.total_latency_ms)
                            if output is not None
                            else round((time.perf_counter() - started) * 1000)
                        ),
                        model_call_count=output.model_call_count if output else 0,
                        input_token_count=output.input_token_count if output else None,
                        output_token_count=output.output_token_count if output else None,
                    )
            succeeded = [result for result in results if result.status == "succeeded"]
            metric_summary = aggregate_metric_rows(
                [result.metrics for result in succeeded]
            )
            metric_summary.update(
                aggregate_repeat_metrics(
                    [
                        (result.repeat_index, result.metrics)
                        for result in succeeded
                    ]
                )
            )
            metric_summary["failed_case_runs"] = len(results) - len(succeeded)
            metric_summary["category_counts"] = dict(
                Counter(result.category for result in results)
            )
            metric_summary["category_metrics"] = {
                category: aggregate_metric_rows(
                    [
                        result.metrics
                        for result in succeeded
                        if result.category == category
                    ]
                )
                for category in sorted(
                    {result.category for result in succeeded}
                )
            }
            failure_summary = summarize_failures(
                [(result.case_id, result.failure_types) for result in results]
            )
            publish_result = (
                self.publisher.publish(
                    dataset=dataset,
                    experiment_name=experiment.name,
                    profile_version=profile.version,
                    local_experiment_id=experiment.id,
                    results=results,
                )
                if publish_langsmith
                else {"status": "disabled", "experiment_id": None}
            )
            metric_summary["langsmith_status"] = publish_result["status"]
            self.experiments.complete(
                db,
                experiment,
                metric_summary=metric_summary,
                failure_summary=failure_summary,
                langsmith_experiment_id=publish_result.get("experiment_id"),
            )
            self.reports.build(
                experiment_id=experiment.id,
                experiment_name=experiment.name,
                dataset_version=dataset_record.version,
                profile_version=profile.version,
                configuration_hash=profile.configuration_hash,
                metric_summary=metric_summary,
                failure_summary=failure_summary,
                results=results,
            )
            return results
        except Exception as exc:
            self.experiments.fail(db, experiment, exc)
            raise

    @staticmethod
    def _infer_persisted_failures(metrics: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        if metrics.get("hit_rate@10") == 0:
            failures.append("retrieval_miss")
        if metrics.get("false_answer_rate") == 1:
            failures.append("false_answer")
        if metrics.get("false_refusal_rate") == 1:
            failures.append("false_refusal")
        if metrics.get("citation_source_validity", 1) < 1:
            failures.append("citation_invalid")
        return failures
