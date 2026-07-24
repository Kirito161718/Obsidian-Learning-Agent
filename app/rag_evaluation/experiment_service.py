from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import (
    RagEvalCaseResult,
    RagEvalDataset,
    RagEvalExperiment,
    RagRetrievalProfile,
)
from app.rag_evaluation.dataset_loader import DatasetLoader, dataset_content_hash
from app.rag_evaluation.dataset_validator import DatasetValidator
from app.rag_evaluation.schemas import (
    CaseEvaluationResult,
    DatasetValidationResult,
    EvaluationDataset,
)
from app.security.redaction import safe_text_preview, stable_hash
from app.services.crud_errors import ResourceNotFoundError, ServiceDatabaseError


class ExperimentService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def register_dataset(
        self,
        db: Session,
        *,
        dataset: EvaluationDataset,
        dataset_file: str,
        validation: DatasetValidationResult,
    ) -> RagEvalDataset:
        loader = DatasetLoader(self.settings)
        path = loader.resolve(dataset_file)
        storage_path = loader.project_relative_path(path)
        content_hash = dataset_content_hash(dataset)
        existing = (
            db.query(RagEvalDataset)
            .filter(
                RagEvalDataset.name == dataset.name,
                RagEvalDataset.version == dataset.version,
            )
            .one_or_none()
        )
        if existing and existing.content_hash != content_hash:
            raise ValueError(
                "Dataset content changed without a new dataset version"
            )
        row = existing or RagEvalDataset(name=dataset.name, version=dataset.version)
        row.storage_path = storage_path
        row.content_hash = content_hash
        row.case_count = validation.case_count
        row.stale_case_count = validation.stale_case_count
        row.safe_case_count = validation.safe_case_count
        row.status = (
            "validated"
            if validation.valid
            else "stale"
            if validation.stale_case_count
            else "invalid"
        )
        row.updated_at = datetime.now(UTC)
        if existing is None:
            db.add(row)
        self._commit(db, "Failed to register evaluation dataset")
        db.refresh(row)
        return row

    def list_datasets(self, db: Session) -> list[RagEvalDataset]:
        try:
            return (
                db.query(RagEvalDataset)
                .order_by(RagEvalDataset.created_at.desc())
                .all()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list evaluation datasets") from exc

    def get_dataset(self, db: Session, dataset_id: str) -> RagEvalDataset:
        row = db.get(RagEvalDataset, dataset_id)
        if row is None:
            raise ResourceNotFoundError("Evaluation dataset not found")
        return row

    def create_experiment(
        self,
        db: Session,
        *,
        dataset: RagEvalDataset,
        profile: RagRetrievalProfile,
        name: str,
        variant_key: str,
        experiment_type: str,
    ) -> RagEvalExperiment:
        if dataset.status != "validated":
            raise ValueError("Only a validated dataset can be evaluated")
        row = RagEvalExperiment(
            dataset_id=dataset.id,
            profile_id=profile.id,
            name=name[:200],
            variant_key=variant_key[:120],
            experiment_type=experiment_type[:30],
            retrieval_profile_version=profile.version,
            status="pending",
            configuration_snapshot=dict(profile.configuration),
            configuration_hash=profile.configuration_hash,
            metric_summary={},
            failure_summary={},
            langsmith_experiment_name=name[:250],
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("Experiment name already exists") from exc
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError("Failed to create evaluation experiment") from exc
        db.refresh(row)
        return row

    def mark_running(self, db: Session, experiment: RagEvalExperiment) -> None:
        experiment.status = "running"
        experiment.started_at = datetime.now(UTC)
        self._commit(db, "Failed to start evaluation experiment")

    def add_case_result(
        self,
        db: Session,
        *,
        experiment_id: str,
        result: CaseEvaluationResult,
        latency_ms: int | None,
        model_call_count: int,
        input_token_count: int | None,
        output_token_count: int | None,
    ) -> RagEvalCaseResult:
        existing = (
            db.query(RagEvalCaseResult)
            .filter(
                RagEvalCaseResult.experiment_id == experiment_id,
                RagEvalCaseResult.case_id == result.case_id,
                RagEvalCaseResult.repeat_index == result.repeat_index,
            )
            .one_or_none()
        )
        row = existing or RagEvalCaseResult(
            experiment_id=experiment_id,
            case_id=result.case_id,
            repeat_index=result.repeat_index,
        )
        row.category = result.category
        row.metric_values = dict(result.metrics)
        row.retrieval_stats = {
            **dict(result.retrieval_stats),
            "failure_types": list(result.failure_types),
        }
        row.answer_stats = dict(result.answer_stats)
        row.internal_run_id = result.internal_run_id
        row.external_trace_id = result.external_trace_id
        row.latency_ms = latency_ms
        row.model_call_count = model_call_count
        row.input_token_count = input_token_count
        row.output_token_count = output_token_count
        row.status = result.status
        row.error_code = result.error_code
        if existing is None:
            db.add(row)
        self._commit(db, "Failed to store evaluation case result")
        db.refresh(row)
        return row

    def complete(
        self,
        db: Session,
        experiment: RagEvalExperiment,
        *,
        metric_summary: dict[str, Any],
        failure_summary: dict[str, Any],
        langsmith_experiment_id: str | None = None,
    ) -> None:
        experiment.status = "completed"
        experiment.metric_summary = metric_summary
        experiment.failure_summary = failure_summary
        experiment.finished_at = datetime.now(UTC)
        experiment.langsmith_experiment_id = (
            langsmith_experiment_id[:80] if langsmith_experiment_id else None
        )
        if experiment.profile_id:
            profile = db.get(RagRetrievalProfile, experiment.profile_id)
            if profile is not None:
                profile.evaluation_status = "completed"
        self._commit(db, "Failed to complete evaluation experiment")

    def fail(
        self,
        db: Session,
        experiment: RagEvalExperiment,
        exc: Exception,
    ) -> None:
        experiment.status = "failed"
        experiment.error_code = type(exc).__name__[:100]
        experiment.error_preview = safe_text_preview(exc, 500)
        experiment.finished_at = datetime.now(UTC)
        self._commit(db, "Failed to record evaluation failure")

    def cancel(self, db: Session, experiment_id: str) -> RagEvalExperiment:
        experiment = self.get_experiment(db, experiment_id)
        if experiment.status not in {"pending", "running"}:
            raise ValueError("Only pending or running experiments can be cancelled")
        if experiment.status == "pending":
            experiment.status = "cancelled"
            experiment.finished_at = datetime.now(UTC)
        else:
            experiment.status = "cancel_requested"
        self._commit(db, "Failed to request evaluation cancellation")
        db.refresh(experiment)
        return experiment

    def cancellation_requested(
        self, db: Session, experiment: RagEvalExperiment
    ) -> bool:
        db.refresh(experiment)
        return experiment.status in {"cancel_requested", "cancelled"}

    def resume(
        self,
        db: Session,
        experiment: RagEvalExperiment,
        *,
        allow_completed_extension: bool = False,
    ) -> None:
        resumable = experiment.status == "failed"
        if experiment.status == "completed":
            resumable = allow_completed_extension or (
                db.query(RagEvalCaseResult.id)
                .filter(
                    RagEvalCaseResult.experiment_id == experiment.id,
                    RagEvalCaseResult.status != "succeeded",
                )
                .first()
                is not None
            )
        if not resumable:
            raise ValueError(
                "Only a failed experiment or a completed experiment with "
                "failed or missing requested case rows can be resumed"
            )
        experiment.status = "running"
        experiment.error_code = None
        experiment.error_preview = None
        experiment.finished_at = None
        self._commit(db, "Failed to resume evaluation experiment")

    def mark_cancelled(self, db: Session, experiment: RagEvalExperiment) -> None:
        experiment.status = "cancelled"
        experiment.finished_at = datetime.now(UTC)
        self._commit(db, "Failed to cancel evaluation experiment")

    def list_experiments(self, db: Session) -> list[RagEvalExperiment]:
        try:
            return (
                db.query(RagEvalExperiment)
                .order_by(RagEvalExperiment.created_at.desc())
                .limit(self.settings.rag_evaluation_keep_experiments)
                .all()
            )
        except SQLAlchemyError as exc:
            raise ServiceDatabaseError("Failed to list evaluation experiments") from exc

    def get_experiment(self, db: Session, experiment_id: str) -> RagEvalExperiment:
        row = db.get(RagEvalExperiment, experiment_id)
        if row is None:
            raise ResourceNotFoundError("Evaluation experiment not found")
        return row

    def case_results(
        self, db: Session, experiment_id: str
    ) -> list[RagEvalCaseResult]:
        self.get_experiment(db, experiment_id)
        return (
            db.query(RagEvalCaseResult)
            .filter(RagEvalCaseResult.experiment_id == experiment_id)
            .order_by(RagEvalCaseResult.case_id, RagEvalCaseResult.repeat_index)
            .all()
        )

    @staticmethod
    def _commit(db: Session, message: str) -> None:
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise ServiceDatabaseError(message) from exc


class LangSmithExperimentPublisher:
    """Fail-open publisher that never re-runs the RAG application."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client

    def publish(
        self,
        *,
        dataset: EvaluationDataset,
        experiment_name: str,
        profile_version: str,
        local_experiment_id: str,
        results: list[CaseEvaluationResult],
    ) -> dict[str, Any]:
        if not self._enabled():
            return {"status": "disabled", "experiment_id": None}
        safe_cases = [
            case
            for case in dataset.cases
            if case.share_with_langsmith and case.review_status == "reviewed"
        ]
        if not safe_cases:
            return {"status": "no_safe_cases", "experiment_id": None}
        try:
            client = self.client or self._make_client()
            dataset_name = self.settings.rag_evaluation_langsmith_dataset
            try:
                remote_dataset = client.read_dataset(dataset_name=dataset_name)
            except Exception:
                remote_dataset = client.create_dataset(
                    dataset_name,
                    description=(
                        "Privacy-reviewed RAG evaluation subset. Local dataset remains "
                        "the source of truth."
                    ),
                    metadata={"schema_version": "rag-eval-v1"},
                )
            examples = []
            for case in safe_cases:
                example_id = uuid5(
                    NAMESPACE_URL, f"ola-rag-eval:{dataset.version}:{case.id}"
                )
                examples.append(
                    {
                        "id": example_id,
                        "inputs": {"case_id": case.id, "question": case.question},
                        "outputs": {
                            "should_answer": case.should_answer,
                            "expected_answer_points": case.expected_answer_points,
                            "expected_source_hashes": [
                                stable_hash(item.note_id)
                                for item in case.relevance_judgments
                                if item.relevance_grade > 0
                            ],
                        },
                        "metadata": {
                            "category": case.category,
                            "dataset_version": dataset.version,
                        },
                    }
                )
            client.create_examples(
                dataset_id=remote_dataset.id,
                examples=examples,
                max_concurrency=1,
            )
            result_by_case = {
                result.case_id: result
                for result in results
                if result.case_id in {case.id for case in safe_cases}
            }

            def target(inputs: dict[str, Any]) -> dict[str, Any]:
                result = result_by_case.get(str(inputs.get("case_id")))
                metrics = result.metrics if result else {}
                return {
                    "case_id": inputs.get("case_id"),
                    "local_score": metrics.get("recall@10"),
                    "status": result.status if result else "missing",
                }

            def local_score(outputs: dict[str, Any], reference_outputs: dict[str, Any]):
                del reference_outputs
                score = outputs.get("local_score")
                return {
                    "key": "local_recall_at_10",
                    "score": float(score) if isinstance(score, (int, float)) else 0.0,
                }

            published = client.evaluate(
                target,
                data=dataset_name,
                evaluators=[local_score],
                experiment_prefix=experiment_name,
                metadata={
                    "dataset_version": dataset.version,
                    "profile_version": profile_version,
                    "experiment_type": "rag_evaluation",
                    "trace_schema_version": self.settings.langsmith_trace_schema_version,
                    "local_experiment_id": local_experiment_id,
                },
                max_concurrency=1,
                num_repetitions=1,
                blocking=True,
                error_handling="log",
            )
            remote_id = str(
                getattr(published, "experiment_id", None)
                or getattr(published, "experiment_name", None)
                or experiment_name
            )
            return {"status": "submitted", "experiment_id": remote_id}
        except Exception as exc:
            return {
                "status": "failed",
                "experiment_id": None,
                "error_code": type(exc).__name__,
            }

    def _enabled(self) -> bool:
        return bool(
            self.settings.rag_evaluation_langsmith_enabled
            and self.settings.langsmith_observability_requested
            and self.settings.langsmith_api_key.strip()
            and self.settings.langsmith_safe_mode
            and self.settings.langsmith_hide_inputs
            and self.settings.langsmith_hide_outputs
        )

    def _make_client(self):
        from langsmith import Client

        return Client(
            api_key=self.settings.langsmith_api_key,
            api_url=self.settings.langsmith_endpoint,
            workspace_id=self.settings.langsmith_workspace_id,
        )
