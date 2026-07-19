import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import QAHistory
from app.domain.retrieved_chunk import RetrievedChunk

logger = logging.getLogger(__name__)
HISTORY_TEXT_PREVIEW_CHARS = 300


class QAHistoryService:
    def save(
        self,
        db: Session,
        *,
        question: str,
        rewritten_query: str,
        answer: str,
        chunks: list[RetrievedChunk],
        session_id: str | None = None,
    ) -> QAHistory:
        compact_sources = [self._compact_chunk(chunk) for chunk in chunks]
        try:
            qa = QAHistory(
                question=question,
                rewritten_query=rewritten_query,
                answer=answer,
                source_chunks=compact_sources,
                session_id=session_id,
            )
            db.add(qa)
            db.commit()
            db.refresh(qa)
            logger.info("QA history saved: qa_id=%s, source_count=%s", qa.id, len(compact_sources))
            return qa
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Failed to save QA history")
            raise

    def _compact_chunk(self, chunk: RetrievedChunk) -> dict:
        # The historical `text` field is retained for API compatibility, but it is only a preview.
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.source_id if chunk.source_type == "uploaded_document" else None,
            "filename": str(chunk.metadata.get("filename") or chunk.source_name),
            "source_id": chunk.source_id,
            "source_type": chunk.source_type,
            "source_name": chunk.source_name,
            "source_path": chunk.source_path,
            "heading_path": list(chunk.metadata.get("heading_path") or []),
            "section_title": chunk.section_title,
            "line_start": chunk.metadata.get("line_start"),
            "line_end": chunk.metadata.get("line_end"),
            "tags": list(chunk.metadata.get("tags") or []),
            "page_no": chunk.page_no,
            "chunk_index": chunk.chunk_index,
            "score": chunk.score,
            "rerank_score": chunk.rerank_score,
            "text": chunk.text[:HISTORY_TEXT_PREVIEW_CHARS],
        }
