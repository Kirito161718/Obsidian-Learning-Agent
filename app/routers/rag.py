from functools import lru_cache
import inspect
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.rag import AskRequest, AskResponse, RetrieveRequest, RetrieveResponse
from app.services.embedding_service import get_embedding_service
from app.services.llm_service import DeepSeekClient, LLMServiceUnavailable
from app.services.crud_errors import ResourceNotFoundError
from app.services.rag_service import RagService
from app.services.rerank_service import get_rerank_service
from app.services.vector_service import VectorService

router = APIRouter(prefix="/rag", tags=["rag"])
logger = logging.getLogger(__name__)
RAG_UNAVAILABLE_DETAIL = "RAG service is temporarily unavailable. Please check Qdrant and retrieval dependencies."
LLM_UNAVAILABLE_DETAIL = "LLM service is temporarily unavailable"


@lru_cache
def get_rag_service() -> RagService:
    embedding_service = get_embedding_service()
    return RagService(
        embedding_service=embedding_service,
        vector_service=VectorService(vector_size=embedding_service.vector_size),
        rerank_service=get_rerank_service(),
        llm_client=DeepSeekClient(),
    )


@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    try:
        return get_rag_service().ask(request, db)
    except LLMServiceUnavailable as exc:
        logger.warning("LLM service unavailable during RAG ask: %s", str(exc)[:300])
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE_DETAIL) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("RAG ask failed")
        raise HTTPException(status_code=503, detail=RAG_UNAVAILABLE_DETAIL) from exc


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest, db: Session = Depends(get_db)) -> RetrieveResponse:
    logger.info(
        "RAG retrieve called: question=%r, document_id=%s, top_k=%s",
        request.question[:120],
        request.document_id or "-",
        request.top_k,
    )
    try:
        service = get_rag_service()
        parameters = inspect.signature(service.retrieve).parameters
        return service.retrieve(request, db) if "db" in parameters else service.retrieve(request)
    except LLMServiceUnavailable as exc:
        logger.warning("Query rewrite unavailable during RAG retrieve, fallback should have been applied: %s", str(exc)[:300])
        raise HTTPException(status_code=503, detail=LLM_UNAVAILABLE_DETAIL) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("RAG retrieve failed")
        raise HTTPException(status_code=503, detail=RAG_UNAVAILABLE_DETAIL) from exc
