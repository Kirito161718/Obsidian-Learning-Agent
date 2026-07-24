from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CaseCategory = Literal[
    "single_note",
    "multi_note",
    "conversational_rewrite",
    "internal_link",
    "no_answer",
    "conflicting_sources",
]
ReviewStatus = Literal["draft", "reviewed", "stale"]


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10_000)


class SourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["obsidian"] = "obsidian"
    note_ids: list[str] = Field(default_factory=list, max_length=100)


class RelevanceJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=1, max_length=100)
    heading_path: list[str] = Field(default_factory=list, max_length=20)
    relevance_grade: int = Field(ge=0, le=3)

    @field_validator("heading_path")
    @classmethod
    def normalize_heading_path(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,119}$")
    category: CaseCategory
    question: str = Field(min_length=1, max_length=10_000)
    should_answer: bool
    requires_rewrite: bool = False
    source_scope: SourceScope = Field(default_factory=SourceScope)
    relevance_judgments: list[RelevanceJudgment] = Field(default_factory=list)
    expected_answer_points: list[str] = Field(default_factory=list, max_length=50)
    acceptable_citation_note_ids: list[str] = Field(default_factory=list, max_length=100)
    forbidden_citation_note_ids: list[str] = Field(default_factory=list, max_length=100)
    conversation_context: list[ConversationTurn] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=50)
    share_with_langsmith: bool = False
    review_status: ReviewStatus = "draft"

    @field_validator(
        "expected_answer_points",
        "acceptable_citation_note_ids",
        "forbidden_citation_note_ids",
        "tags",
    )
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @model_validator(mode="after")
    def validate_answerability_contract(self) -> "EvaluationCase":
        relevant_note_ids = {
            item.note_id for item in self.relevance_judgments if item.relevance_grade > 0
        }
        if self.should_answer:
            if not relevant_note_ids:
                raise ValueError("Answerable cases require at least one positive relevance judgment")
            if not self.expected_answer_points:
                raise ValueError("Answerable cases require expected_answer_points")
        else:
            if self.expected_answer_points:
                raise ValueError("No-answer cases cannot define expected_answer_points")
            if relevant_note_ids:
                raise ValueError("No-answer cases cannot define positive relevance judgments")
            if self.acceptable_citation_note_ids:
                raise ValueError("No-answer cases cannot define acceptable citations")
        overlap = set(self.acceptable_citation_note_ids) & set(
            self.forbidden_citation_note_ids
        )
        if overlap:
            raise ValueError("A citation cannot be both acceptable and forbidden")
        if self.requires_rewrite and not self.conversation_context:
            raise ValueError("Rewrite cases require conversation_context")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rag-eval-v1"] = "rag-eval-v1"
    name: str = Field(min_length=1, max_length=150)
    version: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> "EvaluationDataset":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Dataset case IDs must be unique")
        return self


class DatasetIssue(BaseModel):
    case_id: str
    code: str
    message: str
    stale: bool = False


class DatasetValidationResult(BaseModel):
    valid: bool
    dataset_hash: str
    case_count: int
    stale_case_count: int
    safe_case_count: int
    category_counts: dict[str, int]
    issues: list[DatasetIssue] = Field(default_factory=list)


class EvaluationRunOutput(BaseModel):
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    pre_rerank: list[dict[str, Any]] = Field(default_factory=list)
    answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    rewrite_triggered: bool = False
    original_query_hit: bool | None = None
    rewritten_query_hit: bool | None = None
    answerable: bool | None = None
    model_call_count: int = 0
    rewrite_call_count: int = 0
    input_token_count: int | None = None
    output_token_count: int | None = None
    retrieval_latency_ms: float = 0
    rewrite_latency_ms: float = 0
    rerank_latency_ms: float = 0
    llm_latency_ms: float = 0
    total_latency_ms: float = 0
    internal_run_id: str | None = None
    external_trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseEvaluationResult(BaseModel):
    case_id: str
    category: str
    repeat_index: int = 0
    status: Literal["succeeded", "failed", "cancelled"]
    metrics: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    retrieval_stats: dict[str, Any] = Field(default_factory=dict)
    answer_stats: dict[str, Any] = Field(default_factory=dict)
    failure_types: list[str] = Field(default_factory=list)
    internal_run_id: str | None = None
    external_trace_id: str | None = None
    error_code: str | None = None
