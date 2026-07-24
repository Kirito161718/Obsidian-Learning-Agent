from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.db.models import (
    QAHistory,
    RagEvalCaseResult,
    RagEvalDataset,
    RagRetrievalProfile,
)
from app.graphs.rag_nodes import RagNodes
from app.rag_evaluation.dataset_loader import dataset_content_hash
from app.rag_evaluation.comparison_service import ComparisonService
from app.rag_evaluation.experiment_service import (
    ExperimentService,
    LangSmithExperimentPublisher,
)
from app.rag_evaluation.maintenance import EvaluationMaintenanceService
from app.rag_evaluation.runner import EvaluationRunner
from app.rag_evaluation.schemas import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunOutput,
)
from app.retrieval.configuration import (
    RetrievalProfileConfiguration,
    ablation_configurations,
)
from app.retrieval.profiles import RetrievalProfileService
from app.services.vector_service import qdrant_transport_kwargs


def dataset() -> EvaluationDataset:
    return EvaluationDataset(
        name="runner-fixed",
        version="v1",
        cases=[
            EvaluationCase.model_validate(
                {
                    "id": "rag-case-001",
                    "category": "single_note",
                    "question": "private question must not be persisted",
                    "should_answer": True,
                    "source_scope": {
                        "type": "obsidian",
                        "note_ids": ["note-1"],
                    },
                    "relevance_judgments": [
                        {
                            "note_id": "note-1",
                            "heading_path": ["Topic"],
                            "relevance_grade": 3,
                        }
                    ],
                    "expected_answer_points": ["fact"],
                    "acceptable_citation_note_ids": ["note-1"],
                    "review_status": "reviewed",
                }
            ),
            EvaluationCase.model_validate(
                {
                    "id": "rag-case-002",
                    "category": "no_answer",
                    "question": "unanswerable",
                    "should_answer": False,
                    "source_scope": {"type": "obsidian", "note_ids": []},
                    "relevance_judgments": [],
                    "expected_answer_points": [],
                    "acceptable_citation_note_ids": [],
                    "review_status": "reviewed",
                }
            ),
        ],
    )


class DisabledPublisher:
    def publish(self, **kwargs):
        return {"status": "disabled", "experiment_id": None}


def fake_case_runner(case, profile, mode):
    del profile
    if case.should_answer:
        item = {
            "payload": {
                "source_id": "note-1",
                "heading_path": ["Topic"],
                "text": "fact",
            }
        }
        return EvaluationRunOutput(
            retrieved=[item],
            pre_rerank=[item],
            answer="fact [来源1]" if mode == "full" else None,
            citations=(
                [{"source_id": "note-1", "heading_path": ["Topic"]}]
                if mode == "full"
                else []
            ),
            answerable=True,
            total_latency_ms=10,
        )
    return EvaluationRunOutput(
        retrieved=[],
        pre_rerank=[],
        answer="根据当前知识库资料，无法确定。" if mode == "full" else None,
        answerable=False,
        total_latency_ms=5,
    )


def records(db_session, tmp_path: Path):
    settings = Settings(
        app_env="test",
        rag_evaluation_data_dir=tmp_path / "data",
        rag_evaluation_report_dir=tmp_path / "reports",
        rag_evaluation_langsmith_enabled=False,
    )
    configuration = RetrievalProfileConfiguration.baseline(settings)
    profile = RagRetrievalProfile(
        profile_key="baseline",
        version="rag-baseline-v1",
        description="baseline",
        configuration=configuration.canonical_dict(),
        configuration_hash=configuration.content_hash(),
        collection_name=configuration.collection_name,
        is_active=True,
        is_baseline=True,
        evaluation_status="pending",
    )
    value = dataset()
    dataset_row = RagEvalDataset(
        name=value.name,
        version=value.version,
        storage_path="data/rag_evaluation/datasets/test.yaml",
        content_hash=dataset_content_hash(value),
        case_count=len(value.cases),
        stale_case_count=0,
        safe_case_count=0,
        status="validated",
    )
    db_session.add_all([profile, dataset_row])
    db_session.commit()
    return settings, value, dataset_row, profile


def test_runner_persists_metrics_not_questions_and_does_not_write_qa(
    db_session, tmp_path
):
    settings, value, dataset_row, profile = records(db_session, tmp_path)
    service = ExperimentService(settings)
    experiment = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-test",
        variant_key="baseline",
        experiment_type="full",
    )
    EvaluationRunner(
        fake_case_runner,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=experiment,
        mode="full",
        publish_langsmith=False,
    )
    rows = db_session.query(RagEvalCaseResult).all()
    assert len(rows) == 2
    assert rows[0].metric_values["recall@10"] == 1
    assert experiment.metric_summary["category_metrics"]["single_note"][
        "recall@10"
    ] == 1
    assert db_session.query(QAHistory).count() == 0
    serialized = repr(
        [
            row.metric_values
            | row.retrieval_stats
            | row.answer_stats
            for row in rows
        ]
    )
    assert "private question" not in serialized
    assert (tmp_path / "reports" / experiment.id / "report.md").is_file()
    assert "## Category metrics" in (
        tmp_path / "reports" / experiment.id / "report.md"
    ).read_text(encoding="utf-8")


def test_runner_continues_after_case_failure(db_session, tmp_path):
    settings, value, dataset_row, profile = records(db_session, tmp_path)

    def failing(case, profile, mode):
        if case.id == "rag-case-001":
            raise RuntimeError("synthetic")
        return fake_case_runner(case, profile, mode)

    service = ExperimentService(settings)
    experiment = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-failure-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    results = EvaluationRunner(
        failing,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=experiment,
        publish_langsmith=False,
    )
    assert [result.status for result in results] == ["failed", "succeeded"]
    assert experiment.status == "completed"
    assert experiment.metric_summary["failed_case_runs"] == 1

    resumed = EvaluationRunner(
        fake_case_runner,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=experiment,
        publish_langsmith=False,
    )
    assert all(result.status == "succeeded" for result in resumed)
    assert experiment.metric_summary["failed_case_runs"] == 0


def test_runner_retries_failed_rows_when_resuming(db_session, tmp_path):
    settings, value, dataset_row, profile = records(db_session, tmp_path)
    service = ExperimentService(settings)
    experiment = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-resume-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    service.mark_running(db_session, experiment)
    service.add_case_result(
        db_session,
        experiment_id=experiment.id,
        result=CaseEvaluationResult(
            case_id="rag-case-001",
            category="single_note",
            status="failed",
            error_code="TransientError",
        ),
        latency_ms=1,
        model_call_count=0,
        input_token_count=None,
        output_token_count=None,
    )

    results = EvaluationRunner(
        fake_case_runner,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=experiment,
        publish_langsmith=False,
    )

    rows = (
        db_session.query(RagEvalCaseResult)
        .filter(RagEvalCaseResult.experiment_id == experiment.id)
        .all()
    )
    assert len(rows) == 2
    assert all(row.status == "succeeded" for row in rows)
    assert all(result.status == "succeeded" for result in results)


def test_runner_rejects_mode_mismatch(db_session, tmp_path):
    settings, value, dataset_row, profile = records(db_session, tmp_path)
    service = ExperimentService(settings)
    experiment = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-mode-mismatch-test",
        variant_key="baseline",
        experiment_type="full",
    )
    with pytest.raises(ValueError, match="mode"):
        EvaluationRunner(
            fake_case_runner,
            settings=settings,
            publisher=DisabledPublisher(),
        ).run(
            db_session,
            dataset_record=dataset_row,
            dataset=value,
            profile=profile,
            experiment=experiment,
            mode="retrieval",
            publish_langsmith=False,
        )


def test_runner_filters_cases_and_honors_cancellation(db_session, tmp_path):
    settings, value, dataset_row, profile = records(db_session, tmp_path)
    service = ExperimentService(settings)
    filtered = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-filter-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    filtered_results = EvaluationRunner(
        fake_case_runner,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=filtered,
        categories={"single_note"},
        case_ids={"rag-case-001"},
        publish_langsmith=False,
    )
    assert [result.case_id for result in filtered_results] == ["rag-case-001"]

    cancelled = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-cancel-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    service.mark_running(db_session, cancelled)
    service.cancel(db_session, cancelled.id)
    cancelled_results = EvaluationRunner(
        fake_case_runner,
        settings=settings,
        publisher=DisabledPublisher(),
    ).run(
        db_session,
        dataset_record=dataset_row,
        dataset=value,
        profile=profile,
        experiment=cancelled,
        publish_langsmith=False,
    )
    assert cancelled_results == []
    assert cancelled.status == "cancelled"

    invalid = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-repeat-limit-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    with pytest.raises(ValueError, match="repeat_count"):
        EvaluationRunner(
            fake_case_runner,
            settings=settings,
            publisher=DisabledPublisher(),
        ).run(
            db_session,
            dataset_record=dataset_row,
            dataset=value,
            profile=profile,
            experiment=invalid,
            repeat_count=21,
            publish_langsmith=False,
        )

    pending = service.create_experiment(
        db_session,
        dataset=dataset_row,
        profile=profile,
        name="runner-pending-cancel-test",
        variant_key="baseline",
        experiment_type="retrieval",
    )
    assert service.cancel(db_session, pending.id).status == "cancelled"


def test_langsmith_publisher_uploads_only_reviewed_opt_in_cases(tmp_path):
    value = dataset()
    value.cases[0].question = "What is a public retrieval fact?"
    value.cases[0].share_with_langsmith = True
    value.cases[1].share_with_langsmith = False

    class FakeClient:
        def __init__(self):
            self.examples = []
            self.outputs = []

        def read_dataset(self, **kwargs):
            return SimpleNamespace(id="dataset-id")

        def create_examples(self, **kwargs):
            self.examples = kwargs["examples"]

        def evaluate(self, target, **kwargs):
            self.outputs = [target(item["inputs"]) for item in self.examples]
            return SimpleNamespace(experiment_id="experiment-id")

    client = FakeClient()
    settings = Settings(
        app_env="test",
        rag_evaluation_data_dir=tmp_path / "data",
        rag_evaluation_report_dir=tmp_path / "reports",
        rag_evaluation_langsmith_enabled=True,
        langsmith_tracing=True,
        langsmith_observability_enabled=True,
        langsmith_api_key="test-key",
        langsmith_safe_mode=True,
        langsmith_hide_inputs=True,
        langsmith_hide_outputs=True,
    )
    result = LangSmithExperimentPublisher(settings, client=client).publish(
        dataset=value,
        experiment_name="safe-publish",
        profile_version="rag-baseline-v1",
        local_experiment_id="local-id",
        results=[
            CaseEvaluationResult(
                case_id="rag-case-001",
                category="single_note",
                status="succeeded",
                metrics={"recall@10": 1.0},
            )
        ],
    )
    assert result == {"status": "submitted", "experiment_id": "experiment-id"}
    assert [item["inputs"]["case_id"] for item in client.examples] == [
        "rag-case-001"
    ]
    serialized = repr(client.examples)
    assert "What is a public retrieval fact?" in serialized
    assert "unanswerable" not in serialized
    assert "note-1" not in serialized
    assert client.outputs == [
        {
            "case_id": "rag-case-001",
            "local_score": 1.0,
            "status": "succeeded",
        }
    ]


def test_comparison_supports_six_experiments():
    experiments = [
        {
            "id": f"experiment-{index}",
            "name": f"variant-{index}",
            "retrieval_profile_version": f"v{index}",
            "metric_summary": {"recall@10": index / 10},
        }
        for index in range(6)
    ]
    comparison = ComparisonService.compare(experiments)
    assert len(comparison["experiments"]) == 6
    assert comparison["experiments"][-1]["delta_from_baseline"]["recall@10"] == 0.5


def test_cleanup_dry_run_protects_production_collection(db_session, tmp_path):
    settings, _, _, _ = records(db_session, tmp_path)
    settings.rag_evaluation_keep_collections = 1
    artifact = settings.rag_evaluation_artifact_dir / "old-experiment"
    artifact.mkdir(parents=True)

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(
                collections=[
                    SimpleNamespace(name="rag_chunks"),
                    SimpleNamespace(name="rag_eval_old"),
                    SimpleNamespace(name="rag_eval_keep"),
                ]
            )

    result = EvaluationMaintenanceService(
        settings,
        qdrant_client=FakeQdrant(),
    ).cleanup(db_session, dry_run=True)
    assert result["collections"] == ["rag_eval_keep"]
    assert "rag_chunks" not in result["collections"]
    assert result["artifact_directories"] >= 1
    assert artifact.is_dir()


def test_profile_hash_and_ablation_profiles_are_isolated():
    baseline = RetrievalProfileConfiguration()
    variants = ablation_configurations(baseline)
    assert variants["baseline"].content_hash() == baseline.content_hash()
    assert variants["no-rewrite"].rewrite_enabled is False
    assert variants["no-rerank"].rerank_enabled is False
    assert baseline.rewrite_enabled is True
    assert baseline.dual_retrieval_enabled is True


def test_profile_activation_is_guarded_and_preserves_baseline(
    db_session, tmp_path
):
    settings, _, _, baseline = records(db_session, tmp_path)
    service = RetrievalProfileService(settings)
    candidate_config = RetrievalProfileConfiguration().model_copy(
        update={"neighbor_expansion_enabled": True}
    )
    candidate = service.create(
        db_session,
        profile_key="candidate",
        version="rag-candidate-v1",
        description="candidate",
        configuration=candidate_config,
    )
    with pytest.raises(ValueError, match="confirm"):
        service.activate(
            db_session,
            candidate.id,
            confirm=False,
            expected_profile_version=candidate.version,
        )
    candidate.evaluation_status = "completed"
    candidate.promotion_eligible = True
    db_session.commit()
    activated = service.activate(
        db_session,
        candidate.id,
        confirm=True,
        expected_profile_version=candidate.version,
    )
    db_session.refresh(baseline)
    assert activated.is_active is True
    assert activated.rollback_profile_id == baseline.id
    assert baseline.is_active is False
    assert baseline.is_baseline is True


def test_evaluation_mode_routes_around_qa_persistence():
    nodes = object.__new__(RagNodes)
    assert nodes.route_persistence({"evaluation_mode": True}) == "end"
    assert nodes.route_persistence({"evaluation_mode": False}) == "persist"


def test_loopback_qdrant_bypasses_os_proxy_only():
    assert qdrant_transport_kwargs("http://127.0.0.1:6333") == {
        "trust_env": False
    }
    assert qdrant_transport_kwargs("http://localhost:6333") == {
        "trust_env": False
    }
    assert qdrant_transport_kwargs("https://qdrant.example.com") == {}


def test_rag_evaluation_page_and_summary_apis_do_not_expose_secrets(
    client, db_session
):
    response = client.get("/agent/rag-evaluation")
    assert response.status_code == 200
    assert "RAG评估与优化" in response.text
    assert "LANGSMITH_API_KEY" not in response.text
    assert "OBSIDIAN_VAULT_PATH" not in response.text

    profile_response = client.get("/agent/rag-evaluation/profiles")
    assert profile_response.status_code == 200
    body = profile_response.text
    assert "api_key" not in body.casefold()
    assert "vault_path" not in body.casefold()
    assert client.get("/agent/rag-evaluation/datasets/missing").status_code == 404
