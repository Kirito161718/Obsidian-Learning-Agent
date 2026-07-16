import logging
import math

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import QAHistory
from app.schemas.rag import AskRequest, AskResponse, RetrievedChunk, RetrieveResponse, SourceChunk
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import DeepSeekClient
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService

logger = logging.getLogger(__name__)


def truncate_log_text(value: str, limit: int = 120) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def optional_float(value) -> float | None:
    if value is None:
        return None
    score = float(value)
    return score if math.isfinite(score) else None

NO_CONTEXT_ANSWER = "根据当前知识库资料，无法确定"


class RagService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
        rerank_service: RerankService,
        llm_client: DeepSeekClient,
    ) -> None:
        self.embedding_service = embedding_service
        self.vector_service = vector_service
        self.rerank_service = rerank_service
        self.llm_client = llm_client

    def rewrite_query(self, question: str) -> str:
        normalized_question = " ".join(question.split()).strip()
        if not normalized_question:
            return question

        rewrite = getattr(self.llm_client, "rewrite_query", None)
        if rewrite is None:
            return normalized_question

        try:
            rewritten_query = rewrite(normalized_question)
        except Exception:
            return normalized_question

        rewritten_query = " ".join(rewritten_query.split()).strip()
        return rewritten_query or normalized_question

    def ask(self, request: AskRequest, db: Session) -> AskResponse:
        reranked, rewritten_query = self.retrieve_context(
            question=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
            use_query_rewrite=True,
            return_query=True,
        )
        answer = NO_CONTEXT_ANSWER if not reranked else self.llm_client.generate_answer(request.question, reranked)
        sources = [self._to_source_chunk(item) for item in reranked]

        try:
            qa = QAHistory(
                question=request.question,
                rewritten_query=rewritten_query,
                answer=answer,
                source_chunks=[source.model_dump() for source in sources],
            )
            db.add(qa)
            db.commit()
            logger.info("QA history saved: qa_id=%s, source_count=%s", qa.id, len(sources))
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Failed to save QA history")
            raise

        return AskResponse(answer=answer, sources=sources)

    def retrieve_context(
        self,
        question: str,
        document_id: str | None = None,
        top_k: int = 5,
        *,
        use_query_rewrite: bool = True,
        return_query: bool = False,
    ) -> list[dict] | tuple[list[dict], str]:
        query = self.rewrite_query(question) if use_query_rewrite else " ".join(question.split()).strip()
        query_vector = self.embedding_service.embed_query(query)
        retrieve_top_k = max(top_k * 5, 20)
        logger.info("Qdrant initial retrieve top_k=%s, final top_k=%s", retrieve_top_k, top_k)
        candidates = self.vector_service.search(
            query_vector=query_vector,
            top_k=retrieve_top_k,
            document_id=document_id,
        )
        logger.info(
            "RAG retrieval completed: question=%r, rewritten_query=%r, retrieved_count=%s, top_k=%s, document_id=%s",
            truncate_log_text(question),
            truncate_log_text(query),
            len(candidates),
            top_k,
            document_id or "-",
        )
        reranked = self.rerank_service.rerank(query, candidates, top_k)
        if return_query:
            return reranked, query
        return reranked

    def retrieve(self, request: AskRequest) -> RetrieveResponse:
        chunks, rewritten_query = self.retrieve_context(
            question=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
            use_query_rewrite=True,
            return_query=True,
        )
        retrieved_chunks = [self._to_retrieved_chunk(item) for item in chunks]
        logger.info(
            "RAG retrieve response prepared: question=%r, rewritten_query=%r, document_id=%s, top_k=%s, chunk_count=%s",
            truncate_log_text(request.question),
            truncate_log_text(rewritten_query),
            request.document_id or "-",
            request.top_k,
            len(retrieved_chunks),
        )
        return RetrieveResponse(
            question=request.question,
            rewritten_query=rewritten_query,
            document_id=request.document_id,
            top_k=request.top_k,
            retrieved_count=len(retrieved_chunks),
            chunks=retrieved_chunks,
        )

    def _to_source_chunk(self, item: dict) -> SourceChunk:
        payload = item.get("payload", {})
        return SourceChunk(
            chunk_id=payload.get("chunk_id"),
            document_id=payload.get("document_id", ""),
            filename=payload.get("filename", ""),
            page_no=payload.get("page_no"),
            chunk_index=int(payload.get("chunk_index", 0)),
            score=float(item.get("score", 0.0)),
            rerank_score=optional_float(item.get("rerank_score")),
            text=payload.get("text", ""),
        )

    def _to_retrieved_chunk(self, item: dict) -> RetrievedChunk:
        payload = item.get("payload", {})
        return RetrievedChunk(
            chunk_id=payload.get("chunk_id"),
            document_id=payload.get("document_id", ""),
            filename=payload.get("filename", ""),
            file_type=payload.get("file_type"),
            page_no=payload.get("page_no"),
            section_title=payload.get("section_title"),
            chunk_index=int(payload.get("chunk_index", 0)),
            score=float(item.get("score", 0.0)),
            rerank_score=optional_float(item.get("rerank_score")),
            text=payload.get("text", ""),
        )
