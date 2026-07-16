from typing import TypedDict

from sqlalchemy.orm import Session

from app.domain.retrieved_chunk import RetrievedChunk


class RagState(TypedDict, total=False):
    run_id: str
    mode: str
    question: str
    original_query: str
    rewritten_query: str | None
    rewrite_applied: bool
    rewrite_reason: str | None
    rewrite_enabled: bool | None
    document_id: str | None
    top_k: int
    retrieval_scope: dict
    original_results: list[RetrievedChunk]
    rewritten_results: list[RetrievedChunk]
    merged_results: list[RetrievedChunk]
    expanded_results: list[RetrievedChunk]
    reranked_results: list[RetrievedChunk]
    context_chunks: list[RetrievedChunk]
    context_text: str
    context_sources: list[dict]
    answer: str
    citations: list[dict]
    retrieval_quality: dict
    evaluation_result: dict
    model_call_count: int
    tool_call_count: int
    retry_count: int
    answerable: bool
    error_code: str | None
    error_message: str | None


class RagRuntimeContext(TypedDict, total=False):
    db: Session | None
