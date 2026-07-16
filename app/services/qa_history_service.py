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
    ) -> None:
        compact_sources = [self._compact_chunk(chunk) for chunk in chunks]
        try:
            qa = QAHistory(
                question=question,
                rewritten_query=rewritten_query,
                answer=answer,
                source_chunks=compact_sources,
            )
            db.add(qa)
            db.commit()
            logger.info("QA history saved: qa_id=%s, source_count=%s", qa.id, len(compact_sources))
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Failed to save QA history")
            raise

    def _compact_chunk(self, chunk: RetrievedChunk) -> dict:
        # The historical `text` field is retained for API compatibility, but it is only a preview.
        return {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.source_id,
            "filename": chunk.source_name,
            "section_title": chunk.section_title,
            "page_no": chunk.page_no,
            "chunk_index": chunk.chunk_index,
            "score": chunk.score,
            "rerank_score": chunk.rerank_score,
            "text": chunk.text[:HISTORY_TEXT_PREVIEW_CHARS],
        }
