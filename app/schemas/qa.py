from datetime import datetime

from pydantic import BaseModel


class QAHistorySourceChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    section_title: str | None = None
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
