from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    document_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    source_types: list[Literal["uploaded_document", "obsidian_note"]] | None = None
    source_path_prefixes: list[str] | None = None
    session_id: str | None = None
    goal_id: str | None = None
    learning_status_id: str | None = None

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value

    @field_validator("source_types")
    @classmethod
    def dedupe_source_types(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value else None

    @field_validator("source_path_prefixes")
    @classmethod
    def validate_source_path_prefixes(cls, value: list[str] | None) -> list[str] | None:
        if not value:
            return None
        normalized: list[str] = []
        for raw in value:
            path = raw.replace("\\", "/").strip("/")
            first = path.split("/", 1)[0] if path else ""
            if not path or raw.startswith(("/", "\\")) or ":" in first or ".." in path.split("/"):
                raise ValueError("source_path_prefixes must be safe Vault-relative paths")
            normalized.append(path)
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_source_scope(self) -> "AskRequest":
        if self.document_id and self.source_path_prefixes:
            raise ValueError("document_id cannot be combined with Obsidian path prefixes")
        if self.document_id and self.source_types and self.source_types != ["uploaded_document"]:
            raise ValueError("document_id can only target uploaded_document sources")
        return self


class SourceChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str
    source_type: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_path: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    section_title: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    tags: list[str] = Field(default_factory=list)
    page_no: int | None = None
    chunk_index: int
    score: float
    rerank_score: float | None = None
    text: str


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]
    session_id: str | None = None
    qa_history_id: str | None = None
    learning_context_applied: bool = False
    run_id: str | None = None
    context_manifest: dict = Field(default_factory=dict)


class RetrieveRequest(AskRequest):
    pass


class RetrievedChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str
    source_type: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_path: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    file_type: str | None = None
    page_no: int | None = None
    section_title: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    tags: list[str] = Field(default_factory=list)
    chunk_index: int
    score: float
    rerank_score: float | None = None
    text: str


class RetrieveResponse(BaseModel):
    question: str
    rewritten_query: str
    rewrite_applied: bool = False
    rewrite_reason: str | None = None
    document_id: str | None = None
    top_k: int
    retrieved_count: int
    chunks: list[RetrievedChunk]
    session_id: str | None = None
    run_id: str | None = None
    context_manifest: dict = Field(default_factory=dict)
