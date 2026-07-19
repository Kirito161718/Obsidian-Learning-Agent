from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.retrieved_chunk import RetrievedChunk
from app.graphs.rag_graph import RagWorkflow
from app.graphs.rag_nodes import NO_CONTEXT_ANSWER
from app.schemas.rag import (
    AskRequest,
    AskResponse,
    RetrievedChunk as ApiRetrievedChunk,
    RetrieveResponse,
    SourceChunk,
)
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import DeepSeekClient
from app.services.rerank_service import RerankService
from app.services.vector_service import VectorService


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
        self.workflow = RagWorkflow(
            settings=get_settings(),
            embedding_service=embedding_service,
            vector_service=vector_service,
            rerank_service=rerank_service,
            llm_client=llm_client,
        )

    def rewrite_query(self, question: str) -> str:
        """Backward-compatible helper; the workflow only calls it after quality assessment."""
        normalized = " ".join(question.split()).strip()
        rewrite = getattr(self.llm_client, "rewrite_query", None)
        if not normalized or rewrite is None:
            return normalized or question
        try:
            rewritten = " ".join(rewrite(normalized).split()).strip()
        except Exception:
            return normalized
        return rewritten or normalized

    def ask(self, request: AskRequest, db: Session) -> AskResponse:
        state = self.workflow.invoke(
            mode="ask",
            question=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
            source_types=request.source_types,
            source_path_prefixes=request.source_path_prefixes,
            session_id=request.session_id,
            goal_id=request.goal_id,
            learning_status_id=request.learning_status_id,
            db=db,
        )
        sources = [self._to_source_chunk(chunk) for chunk in state.get("context_chunks", [])]
        return AskResponse(
            answer=state.get("answer") or NO_CONTEXT_ANSWER,
            sources=sources,
            session_id=request.session_id,
            qa_history_id=state.get("qa_history_id"),
            learning_context_applied=bool(state.get("learning_context")),
        )

    def retrieve_context(
        self,
        question: str,
        document_id: str | None = None,
        top_k: int = 5,
        *,
        use_query_rewrite: bool = True,
        return_query: bool = False,
    ) -> list[dict] | tuple[list[dict], str]:
        """Compatibility entry point for the existing retrieval evaluation script."""
        state = self.workflow.invoke(
            mode="retrieve",
            question=question,
            document_id=document_id,
            top_k=top_k,
            rewrite_enabled=use_query_rewrite,
        )
        chunks = [chunk.to_rerank_item() for chunk in state.get("context_chunks", [])]
        query = state.get("rewritten_query") or state.get("original_query") or question.strip()
        return (chunks, query) if return_query else chunks

    def retrieve(self, request: AskRequest, db: Session | None = None) -> RetrieveResponse:
        state = self.workflow.invoke(
            mode="retrieve",
            question=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
            source_types=request.source_types,
            source_path_prefixes=request.source_path_prefixes,
            session_id=request.session_id,
            goal_id=request.goal_id,
            learning_status_id=request.learning_status_id,
            db=db,
        )
        chunks = [self._to_retrieved_chunk(chunk) for chunk in state.get("context_chunks", [])]
        return RetrieveResponse(
            question=request.question,
            rewritten_query=state.get("rewritten_query") or state.get("original_query") or request.question.strip(),
            rewrite_applied=state.get("rewrite_applied", False),
            rewrite_reason=state.get("rewrite_reason"),
            document_id=request.document_id,
            top_k=request.top_k,
            retrieved_count=len(chunks),
            chunks=chunks,
            session_id=request.session_id,
        )

    def _to_source_chunk(self, chunk: RetrievedChunk) -> SourceChunk:
        return SourceChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.source_id if chunk.source_type == "uploaded_document" else None,
            filename=str(chunk.metadata.get("filename") or chunk.source_name),
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_name=chunk.source_name,
            source_path=chunk.source_path,
            heading_path=list(chunk.metadata.get("heading_path") or []),
            section_title=chunk.section_title,
            line_start=chunk.metadata.get("line_start"),
            line_end=chunk.metadata.get("line_end"),
            tags=list(chunk.metadata.get("tags") or []),
            page_no=chunk.page_no,
            chunk_index=chunk.chunk_index or 0,
            score=chunk.score or 0.0,
            rerank_score=chunk.rerank_score,
            text=chunk.text,
        )

    def _to_retrieved_chunk(self, chunk: RetrievedChunk) -> ApiRetrievedChunk:
        return ApiRetrievedChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.source_id if chunk.source_type == "uploaded_document" else None,
            filename=str(chunk.metadata.get("filename") or chunk.source_name),
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_name=chunk.source_name,
            source_path=chunk.source_path,
            heading_path=list(chunk.metadata.get("heading_path") or []),
            file_type=chunk.metadata.get("file_type"),
            page_no=chunk.page_no,
            section_title=chunk.section_title,
            line_start=chunk.metadata.get("line_start"),
            line_end=chunk.metadata.get("line_end"),
            tags=list(chunk.metadata.get("tags") or []),
            chunk_index=chunk.chunk_index or 0,
            score=chunk.score or 0.0,
            rerank_score=chunk.rerank_score,
            text=chunk.text,
        )
