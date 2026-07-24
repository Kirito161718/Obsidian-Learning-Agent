from types import SimpleNamespace

from app.core.config import Settings
from app.domain.retrieved_chunk import RetrievedChunk
from app.graphs.rag_nodes import NO_CONTEXT_ANSWER, RagNodes
from app.db.models import RagRetrievalProfile
from app.observability.metadata import rag_result_metadata
from app.observability.sanitization import sanitize_metadata
from app.rag_evaluation.dataset_loader import dataset_content_hash
from app.rag_evaluation.dataset_validator import (
    DatasetValidator,
    VaultCatalogEntry,
)
from app.rag_evaluation.safety_calibration import (
    SafetyCalibrationService,
    SafetyObservation,
)
from app.rag_evaluation.schemas import EvaluationCase, EvaluationDataset
from app.retrieval.evidence_sufficiency import EvidenceSufficiencyService
from app.services.llm_service import DeepSeekClient, SAFETY_SYSTEM_PROMPT
from app.services.rerank_service import get_rerank_service
from app.services.rag_service import RagService
from app.retrieval.configuration import RetrievalProfileConfiguration


def chunk(
    chunk_id: str,
    note_id: str,
    score: float,
    *,
    rerank_score: float | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=note_id,
        source_type="obsidian_note",
        source_name=f"{note_id}.md",
        source_path=f"notes/{note_id}.md",
        text="evidence " * 30,
        score=score,
        rerank_score=rerank_score,
    )


def evaluate(
    *,
    chunks: list[RetrievedChunk],
    question: str = "What is supported?",
    thresholds: dict[str, float] | None = None,
):
    service = EvidenceSufficiencyService()
    return service.evaluate(
        original_results=chunks,
        rewritten_results=[],
        candidates=chunks,
        reranked=chunks,
        context_chunks=chunks,
        context_text="\n".join(item.text for item in chunks),
        context_sources=[
            {"source_id": item.source_id, "citation_index": index}
            for index, item in enumerate(chunks, 1)
        ],
        source_types=["obsidian_note"],
        source_path_prefixes=None,
        question=question,
        thresholds=thresholds,
        calibration_version="evidence-v1",
    )


def safety_case(
    case_id: str,
    *,
    policy: str,
    split: str,
    note_id: str = "note-1",
) -> EvaluationCase:
    should_answer = policy != "abstain"
    return EvaluationCase.model_validate(
        {
            "id": case_id,
            "category": (
                "no_answer"
                if policy == "abstain"
                else "conflicting_sources"
                if policy == "answer_with_uncertainty"
                else "single_note"
            ),
            "question": f"fixture {case_id}",
            "should_answer": should_answer,
            "source_scope": {
                "type": "obsidian",
                "note_ids": [note_id] if should_answer else [],
            },
            "relevance_judgments": (
                [
                    {
                        "note_id": note_id,
                        "heading_path": [],
                        "relevance_grade": 3,
                    }
                ]
                if should_answer
                else []
            ),
            "expected_answer_points": ["fact"] if should_answer else [],
            "acceptable_citation_note_ids": (
                [note_id] if should_answer else []
            ),
            "review_status": "reviewed",
            "safety_split": split,
            "expected_policy": policy,
        }
    )


def feature_values(
    *,
    top: float,
    mean: float,
    conflict: bool = False,
) -> dict:
    return {
        "candidate_count": 5,
        "reranked_count": 5,
        "distinct_note_count": 2 if conflict else 1,
        "top_score": top,
        "top_k_score_mean": mean,
        "rewritten_retrieval_hit": False,
        "retrieval_agreement": 1.0,
        "context_chunk_count": 2,
        "context_char_count": 500,
        "valid_source_count": 2 if conflict else 1,
        "source_scope_match": True,
        "conflict_intent": conflict,
    }


def test_v2_dataset_requires_split_and_policy_and_hash_is_stable():
    cases = [
        safety_case("case-answer-cal", policy="answer", split="calibration"),
        safety_case("case-none-cal", policy="abstain", split="calibration"),
        safety_case(
            "case-conflict-cal",
            policy="answer_with_uncertainty",
            split="calibration",
        ),
        safety_case("case-answer-hold", policy="answer", split="holdout"),
        safety_case("case-none-hold", policy="abstain", split="holdout"),
        safety_case(
            "case-conflict-hold",
            policy="answer_with_uncertainty",
            split="holdout",
        ),
    ]
    dataset = EvaluationDataset(
        schema_version="rag-eval-v2",
        name="safe-fixture",
        version="v2",
        cases=cases,
    )
    copy = EvaluationDataset.model_validate(dataset.model_dump())
    assert dataset_content_hash(dataset) == dataset_content_hash(copy)
    result = DatasetValidator(
        {
            "note-1": VaultCatalogEntry(
                status="indexed", headings={()}
            )
        }
    ).validate(dataset)
    assert result.valid is True


def test_v2_validator_rejects_incomplete_holdout():
    dataset = EvaluationDataset(
        schema_version="rag-eval-v2",
        name="safe-fixture",
        version="v2",
        cases=[
            safety_case("case-answer-cal", policy="answer", split="calibration"),
            safety_case("case-none-cal", policy="abstain", split="calibration"),
            safety_case(
                "case-conflict-cal",
                policy="answer_with_uncertainty",
                split="calibration",
            ),
            safety_case("case-answer-hold", policy="answer", split="holdout"),
        ],
    )
    result = DatasetValidator(
        {
            "note-1": VaultCatalogEntry(
                status="indexed", headings={()}
            )
        }
    ).validate(dataset)
    assert result.valid is False
    assert {item.code for item in result.issues} == {
        "safety_split_incomplete"
    }


def test_evidence_gate_abstains_without_candidates():
    decision = evaluate(chunks=[])
    assert decision.policy == "abstain"
    assert "no_candidates" in decision.reason_codes


def test_evidence_gate_combines_top_and_mean_scores():
    decision = evaluate(
        chunks=[chunk("c1", "n1", 0.2), chunk("c2", "n1", 0.1)],
        thresholds={"min_top_score": 0.5, "min_mean_score": 0.4},
    )
    assert decision.policy == "abstain"
    assert {"weak_top_score", "weak_score_mean"} <= set(
        decision.reason_codes
    )


def test_evidence_gate_allows_supported_boundary_answer():
    decision = evaluate(
        chunks=[chunk("c1", "n1", 0.51), chunk("c2", "n1", 0.2)],
        thresholds={"min_top_score": 0.5, "min_mean_score": 0.4},
    )
    assert decision.policy == "answer"


def test_evidence_gate_routes_conflict_to_uncertainty():
    decision = evaluate(
        chunks=[chunk("c1", "n1", 0.9), chunk("c2", "n2", 0.8)],
        question="Do these sources conflict?",
    )
    assert decision.policy == "answer_with_uncertainty"
    assert decision.reason_codes == ("conflicting_evidence",)


def test_calibration_ignores_holdout_and_prevents_all_abstain():
    service = SafetyCalibrationService()
    calibration = [
        SafetyObservation(
            f"a{index}", "calibration", "answer", feature_values(top=0.9, mean=0.8)
        )
        for index in range(5)
    ] + [
        SafetyObservation(
            f"n{index}", "calibration", "abstain", feature_values(top=0.2, mean=0.1)
        )
        for index in range(3)
    ] + [
        SafetyObservation(
            "c1",
            "calibration",
            "answer_with_uncertainty",
            feature_values(top=0.8, mean=0.7, conflict=True),
        )
    ]
    first = service.calibrate(
        dataset_hash="a" * 64,
        observations=calibration
        + [
            SafetyObservation(
                "hold",
                "holdout",
                "abstain",
                feature_values(top=99, mean=99),
            )
        ],
    )
    second = service.calibrate(
        dataset_hash="a" * 64,
        observations=calibration
        + [
            SafetyObservation(
                "hold",
                "holdout",
                "answer",
                feature_values(top=-99, mean=-99),
            )
        ],
    )
    assert first["thresholds"] == second["thresholds"]
    assert first["holdout_used_for_tuning"] is False
    assert first["eligible"] is True
    assert (
        service.passes_core_gates(
            {
                "no_answer_accuracy": 1.0,
                "false_answer_rate": 0.0,
                "answerability_accuracy": 0.4,
                "false_refusal_rate": 1.0,
                "answer_rate": 0.0,
            }
        )
        is False
    )


def test_post_citation_gate_downgrades_and_blocks_persistence():
    nodes = object.__new__(RagNodes)
    state = {
        "answer": "unsupported",
        "citations": [],
        "evaluation_result": {"invalid_citations": []},
        "retrieval_profile": {"post_citation_gate_enabled": True},
        "evidence_reason_codes": [],
    }
    result = nodes.enforce_answer_safety(state)
    assert result["answer"] == NO_CONTEXT_ANSWER
    assert result["answer_policy"] == "abstain"
    assert result["citation_gate_passed"] is False
    assert nodes.route_persistence({**state, **result}) == "end"


def test_safety_prompt_uses_one_model_call_and_uncertainty_policy():
    captured = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="uncertain [来源1]"),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

    client = object.__new__(DeepSeekClient)
    client.model = "test-model"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    result = client.generate_answer(
        "question",
        "[来源1] context",
        answer_policy="answer_with_uncertainty",
        safety_prompt_version="rag-answer-prompt-v2-safety",
    )
    assert result == "uncertain [来源1]"
    assert len(captured) == 1
    assert captured[0]["temperature"] == 0
    assert captured[0]["messages"][0]["content"] == SAFETY_SYSTEM_PROMPT
    assert "answer_with_uncertainty" in captured[0]["messages"][1]["content"]


def test_reranker_service_is_reused():
    get_rerank_service.cache_clear()
    first = get_rerank_service(False, "fixture-model")
    second = get_rerank_service(False, "fixture-model")
    assert first is second


def test_active_profile_switch_reaches_production_rag(db_session):
    configuration = RetrievalProfileConfiguration(
        evidence_gate_enabled=True,
        evidence_thresholds={
            "min_top_score": 0.5,
            "min_mean_score": 0.4,
        },
    )
    db_session.add(
        RagRetrievalProfile(
            profile_key="safe",
            version="rag-safe-v1",
            configuration=configuration.canonical_dict(),
            configuration_hash=configuration.content_hash(),
            collection_name=configuration.collection_name,
            is_active=True,
            is_baseline=False,
        )
    )
    db_session.commit()
    active = RagService._active_profile(db_session)
    assert active["evidence_gate_enabled"] is True
    assert active["evidence_thresholds"]["min_top_score"] == 0.5


def test_prewarm_disabled_does_not_initialize_models(monkeypatch):
    from app.services import rag_model_runtime

    monkeypatch.setattr(
        rag_model_runtime,
        "get_embedding_service",
        lambda: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    status = rag_model_runtime.prewarm_rag_models(
        Settings(app_env="test", rag_model_prewarm_enabled=False)
    )
    assert status["status"] == "disabled"
    assert status["attempted"] is False


def test_prewarm_failure_is_fail_open(monkeypatch):
    from app.services import rag_model_runtime

    rag_model_runtime._status["ready"] = False
    monkeypatch.setattr(
        rag_model_runtime,
        "get_embedding_service",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    status = rag_model_runtime.prewarm_rag_models(
        Settings(app_env="test", rag_model_prewarm_enabled=True)
    )
    assert status["status"] == "failed_open"
    assert status["ready"] is False
    assert status["error_code"] == "RuntimeError"


def test_safety_metadata_contains_decisions_but_not_text():
    metadata = rag_result_metadata(
        {
            "mode": "ask",
            "answerable": False,
            "answer_policy": "abstain",
            "evidence_confidence_band": "low",
            "evidence_reason_codes": ["weak_top_score"],
            "evidence_feature_version": "evidence-v1",
            "abstained_before_llm": True,
            "question": "private question",
            "answer": "private answer",
            "context_text": "private context",
        }
    )
    sanitized = sanitize_metadata(metadata, 10_000)
    assert sanitized["answer_policy"] == "abstain"
    assert sanitized["evidence_reason_codes"] == ["weak_top_score"]
    assert sanitized["abstained_before_llm"] is True
    assert "question" not in sanitized
    assert "answer" not in sanitized
    assert "context_text" not in sanitized


def test_safety_status_and_prewarm_status_api_are_content_free(client):
    safety = client.get("/agent/rag-evaluation/safety/status")
    prewarm = client.get(
        "/agent/rag-evaluation/performance/prewarm/status"
    )
    assert safety.status_code == 200
    assert prewarm.status_code == 200
    payload = safety.json()
    assert set(payload) == {
        "dataset_version",
        "dataset_case_count",
        "split_counts",
        "active_profile",
        "safe_profile",
    }
    assert "question" not in str(payload).casefold()
    assert "answer" not in str(payload).casefold()
