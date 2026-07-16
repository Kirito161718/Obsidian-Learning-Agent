from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int
    file_hash: str | None = None
    message: str | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    file_path: str
    file_hash: str | None = None
    chunk_count: int
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    processed_at: datetime | None = None
    deleted_at: datetime | None = None
    purged_at: datetime | None = None
    error_message: str | None = None


class DocumentLifecycleResponse(BaseModel):
    document_id: str
    status: str
    message: str


class DocumentChunk(BaseModel):
    chunk_id: str | None = None
    chunk_index: int
    page_no: int | None = None
    section_title: str | None = None
    text: str


class DocumentChunksResponse(BaseModel):
    document_id: str
    filename: str
    status: str
    chunk_count: int
    chunks: list[DocumentChunk]
