from pathlib import Path

import pytest
import yaml

from app.core.config import Settings
from app.rag_evaluation.dataset_loader import (
    DatasetLoader,
    dataset_content_hash,
)
from app.rag_evaluation.dataset_validator import DatasetValidator, VaultCatalogEntry
from app.rag_evaluation.errors import DatasetPathError
from app.rag_evaluation.metrics import (
    aggregate_repeat_metrics,
    aggregate_metric_rows,
    answerability_metrics,
    citation_metrics,
    percentile,
    retrieval_metrics,
)
from app.rag_evaluation.promotion_service import PromotionService
from app.rag_evaluation.schemas import EvaluationCase, EvaluationDataset


def case_payload(**updates):
    value = {
        "id": "rag-case-001",
        "category": "single_note",
        "question": "What is described?",
        "should_answer": True,
        "source_scope": {"type": "obsidian", "note_ids": ["note-1"]},
        "relevance_judgments": [
            {
                "note_id": "note-1",
                "heading_path": ["Topic"],
                "relevance_grade": 3,
            }
        ],
        "expected_answer_points": ["expected fact"],
        "acceptable_citation_note_ids": ["note-1"],
        "review_status": "reviewed",
    }
    value.update(updates)
    return value


def retrieved(note_id: str, heading: list[str], *, text: str = "content") -> dict:
    return {
        "score": 0.8,
        "payload": {
            "source_id": note_id,
            "heading_path": heading,
            "text": text,
        },
    }


def test_dataset_schema_enforces_answerability_and_unique_case_ids():
    case = EvaluationCase.model_validate(case_payload())
    dataset = EvaluationDataset(
        name="fixed",
        version="v1",
        cases=[case],
    )
    assert dataset.schema_version == "rag-eval-v1"

    with pytest.raises(ValueError, match="unique"):
        EvaluationDataset(name="fixed", version="v1", cases=[case, case])

    with pytest.raises(ValueError, match="No-answer"):
        EvaluationCase.model_validate(
            case_payload(
                category="no_answer",
                should_answer=False,
                expected_answer_points=["must not exist"],
            )
        )

    with pytest.raises(ValueError, match="conversation_context"):
        EvaluationCase.model_validate(
            case_payload(category="conversational_rewrite", requires_rewrite=True)
        )


def test_dataset_hash_is_stable_and_sensitive_to_content():
    first = EvaluationDataset(
        name="fixed",
        version="v1",
        cases=[EvaluationCase.model_validate(case_payload())],
    )
    second = EvaluationDataset.model_validate(first.model_dump())
    assert dataset_content_hash(first) == dataset_content_hash(second)
    second.cases[0].question = "Changed question"
    assert dataset_content_hash(first) != dataset_content_hash(second)


def test_dataset_loader_rejects_arbitrary_paths(tmp_path: Path):
    root = tmp_path / "data"
    dataset_dir = root / "datasets"
    dataset_dir.mkdir(parents=True)
    dataset = EvaluationDataset(
        name="fixed",
        version="v1",
        cases=[EvaluationCase.model_validate(case_payload())],
    )
    (dataset_dir / "fixed.yaml").write_text(
        yaml.safe_dump(dataset.model_dump(mode="json")),
        encoding="utf-8",
    )
    settings = Settings(
        app_env="test",
        rag_evaluation_data_dir=root,
        rag_evaluation_report_dir=tmp_path / "reports",
    )
    loader = DatasetLoader(settings)
    assert loader.load("fixed.yaml").name == "fixed"
    with pytest.raises(DatasetPathError):
        loader.resolve("../fixed.yaml")
    with pytest.raises(DatasetPathError):
        loader.resolve(tmp_path / "fixed.yaml")
    with pytest.raises(DatasetPathError):
        loader.resolve("fixed.txt")


def test_dataset_validator_detects_stale_notes_and_headings():
    dataset = EvaluationDataset(
        name="fixed",
        version="v1",
        cases=[EvaluationCase.model_validate(case_payload())],
    )
    validator = DatasetValidator(
        {
            "note-1": VaultCatalogEntry(
                status="indexed",
                headings={("Different",)},
            )
        }
    )
    result = validator.validate(dataset)
    assert result.valid is False
    assert result.stale_case_count == 1
    assert result.issues[0].code == "heading_stale"

    validator = DatasetValidator(
        {"note-1": VaultCatalogEntry(status="indexed", headings={("Topic",)})}
    )
    valid = validator.validate(dataset)
    assert valid.valid is True
    assert valid.dataset_hash == dataset_content_hash(dataset)


def test_retrieval_metrics_cover_hit_recall_precision_mrr_and_ndcg():
    case = EvaluationCase.model_validate(case_payload())
    ranked = [
        retrieved("noise", ["Other"]),
        retrieved("note-1", ["Topic"]),
        retrieved("another", ["Other"]),
    ]
    metrics = retrieval_metrics(ranked, case.relevance_judgments, k_values=(2, 3))
    assert metrics["hit_rate@2"] == 1
    assert metrics["recall@2"] == 1
    assert metrics["precision@2"] == pytest.approx(0.5)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert 0 < metrics["ndcg@2"] < 1


def test_note_level_ground_truth_is_not_overcounted():
    judgments = [
        {
            "note_id": "note-1",
            "heading_path": [],
            "relevance_grade": 3,
        }
    ]
    case = EvaluationCase.model_validate(
        case_payload(relevance_judgments=judgments)
    )
    metrics = retrieval_metrics(
        [
            retrieved("note-1", ["A"]),
            retrieved("note-1", ["B"]),
            retrieved("note-1", ["C"]),
        ],
        case.relevance_judgments,
        k_values=(3,),
    )
    assert metrics["recall@3"] == 1
    assert metrics["precision@3"] == pytest.approx(1 / 3)
    assert metrics["ndcg@3"] == 1


def test_retrieval_metrics_without_ground_truth_are_unavailable():
    metrics = retrieval_metrics(
        [retrieved("note-1", ["Topic"])],
        [],
        k_values=(5,),
    )
    assert metrics == {
        "mrr": None,
        "hit_rate@5": None,
        "recall@5": None,
        "precision@5": None,
        "ndcg@5": None,
    }


def test_citation_and_answerability_metrics_cover_edge_cases():
    values = citation_metrics(
        answer="Supported [来源1]",
        citations=[{"source_id": "note-1", "heading_path": ["Topic"]}],
        acceptable_note_ids=["note-1"],
        forbidden_note_ids=[],
        retrieved=[retrieved("note-1", ["Topic"])],
        should_answer=True,
    )
    assert values["citation_precision"] == 1
    assert values["citation_recall"] == 1
    assert values["unsupported_citation_count"] == 0
    assert answerability_metrics(
        should_answer=False, predicted_answerable=True
    )["false_answer_rate"] == 1
    assert answerability_metrics(
        should_answer=True, predicted_answerable=False
    )["false_refusal_rate"] == 1


def test_percentiles_interpolate_and_empty_is_none():
    assert percentile([], 0.95) is None
    assert percentile([10], 0.95) == 10
    assert percentile([0, 100], 0.5) == 50


def test_aggregate_metrics_turn_boolean_case_results_into_rates():
    summary = aggregate_metric_rows(
        [
            {"rewrite_triggered": True, "total_latency_ms": 10},
            {"rewrite_triggered": False, "total_latency_ms": 30},
        ]
    )
    assert summary["rewrite_triggered"] == 0.5
    assert summary["p50_latency"] == 20


def test_repeat_aggregation_uses_between_repeat_summaries():
    summary = aggregate_repeat_metrics(
        [
            (0, {"recall@10": 0.4, "total_latency_ms": 100}),
            (0, {"recall@10": 0.6, "total_latency_ms": 200}),
            (1, {"recall@10": 0.8, "total_latency_ms": 300}),
            (1, {"recall@10": 1.0, "total_latency_ms": 500}),
        ]
    )

    assert summary["repeat_count"] == 2
    assert summary["repeat_summaries"]["0"]["recall@10"] == pytest.approx(0.5)
    assert summary["repeat_summaries"]["1"]["recall@10"] == pytest.approx(0.9)
    assert summary["recall@10_repeat_mean"] == pytest.approx(0.7)
    assert summary["recall@10_repeat_stddev"] == pytest.approx(0.2)
    assert summary["p95_latency_repeat_mean"] == pytest.approx(342.5)


def test_promotion_requires_gain_and_all_safeguards():
    settings = Settings(
        app_env="test",
        rag_promotion_min_recall_gain=0.02,
        rag_promotion_min_ndcg_gain=0.02,
    )
    decision = PromotionService(settings).decide(
        baseline={
            "recall@10": 0.5,
            "ndcg@10": 0.5,
            "citation_precision": 1,
            "no_answer_accuracy": 1,
            "false_answer_rate": 0,
            "p95_latency": 100,
            "model_call_count": 1,
        },
        candidate={
            "recall@10": 0.53,
            "ndcg@10": 0.5,
            "citation_precision": 1,
            "no_answer_accuracy": 1,
            "false_answer_rate": 0,
            "p95_latency": 115,
            "model_call_count": 1,
        },
    )
    assert decision.eligible is True

    rejected = PromotionService(settings).decide(
        baseline={
            "recall@10": 0.5,
            "ndcg@10": 0.5,
            "citation_precision": 1,
            "no_answer_accuracy": 1,
            "false_answer_rate": 0,
            "p95_latency": 100,
            "model_call_count": 1,
        },
        candidate={
            "recall@10": 0.6,
            "ndcg@10": 0.6,
            "citation_precision": 0.8,
            "no_answer_accuracy": 0.5,
            "false_answer_rate": 0.2,
            "p95_latency": 200,
            "model_call_count": 2,
        },
    )
    assert rejected.eligible is False
    assert rejected.safeguards_passed is False
