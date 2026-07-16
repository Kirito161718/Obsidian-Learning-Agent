from pydantic import BaseModel, Field, field_validator


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    document_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class SourceChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str
    filename: str
    source_type: str | None = None
    section_title: str | None = None
    page_no: int | None = None
    chunk_index: int
    score: float
    rerank_score: float | None = None
    text: str


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


class RetrieveRequest(AskRequest):
    pass


class RetrievedChunk(BaseModel):
    chunk_id: str | None = None
    document_id: str
    filename: str
    source_type: str | None = None
    file_type: str | None = None
    page_no: int | None = None
    section_title: str | None = None
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
