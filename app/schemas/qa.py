from datetime import datetime

from pydantic import BaseModel, Field


class QAHistorySourceChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    source_id: str | None = None
    source_type: str | None = None
    source_name: str | None = None
    source_path: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    section_title: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    tags: list[str] = Field(default_factory=list)
    page_no: int | None = None
    chunk_index: int | None = None
    score: float | None = None
    rerank_score: float | None = None
    text: str | None = None


class QAHistoryItem(BaseModel):
    id: str
    question: str
    rewritten_query: str
    answer_preview: str
    created_at: datetime
    source_count: int
    session_id: str | None = None


class QAHistoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[QAHistoryItem]


class QAHistoryDetailResponse(BaseModel):
    id: str
    question: str
    rewritten_query: str
    answer: str
    source_chunks: list[QAHistorySourceChunk]
    created_at: datetime
    session_id: str | None = None
