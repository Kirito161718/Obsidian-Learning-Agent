import math
from typing import Any

from pydantic import BaseModel, Field


def finite_float(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


class RetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    source_type: str = "uploaded_document"
    source_name: str
    source_path: str | None = None
    section_title: str | None = None
    page_no: int | None = None
    chunk_index: int | None = None
    text: str
    score: float | None = None
    rerank_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_search_result(cls, item: dict[str, Any]) -> "RetrievedChunk":
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        source_id = str(payload.get("source_id") or payload.get("document_id") or "")
        raw_chunk_index = payload.get("chunk_index")
        chunk_index = int(raw_chunk_index) if raw_chunk_index is not None else 0
        chunk_id = str(payload.get("chunk_id") or f"{source_id}_{chunk_index}")
        metadata = dict(payload.get("metadata") or {})
        if payload.get("file_type") is not None:
            metadata.setdefault("file_type", payload["file_type"])
        return cls(
            chunk_id=chunk_id,
            source_id=source_id,
            source_type=str(payload.get("source_type") or "uploaded_document"),
            source_name=str(payload.get("source_name") or payload.get("filename") or ""),
            source_path=payload.get("source_path"),
            section_title=payload.get("section_title"),
            page_no=payload.get("page_no"),
            chunk_index=chunk_index,
            text=str(payload.get("text") or ""),
            score=finite_float(item.get("score")),
            rerank_score=finite_float(item.get("rerank_score")),
            metadata=metadata,
        )

    def to_rerank_item(self) -> dict[str, Any]:
        payload = {
            "chunk_id": self.chunk_id,
            "document_id": self.source_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "filename": self.source_name,
            "source_name": self.source_name,
            "section_title": self.section_title,
            "page_no": self.page_no,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "metadata": self.metadata,
        }
        if self.metadata.get("file_type") is not None:
            payload["file_type"] = self.metadata["file_type"]
        return {
            "score": self.score if self.score is not None else 0.0,
            "rerank_score": self.rerank_score,
            "payload": payload,
        }
