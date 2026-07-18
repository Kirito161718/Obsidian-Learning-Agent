import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text, cast, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import QAHistory
from app.schemas.qa import QAHistoryDetailResponse, QAHistoryItem, QAHistoryListResponse, QAHistorySourceChunk

router = APIRouter(prefix="/qa", tags=["qa"])
logger = logging.getLogger(__name__)
DATABASE_UNAVAILABLE_DETAIL = "Database is temporarily unavailable"


def parse_source_chunks(value: object, qa_id: str) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("QA history source_chunks JSON parse failed: qa_id=%s", qa_id)
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        logger.warning("QA history source_chunks is not a list after JSON parse: qa_id=%s", qa_id)
        return []
    logger.warning("QA history source_chunks has unsupported type: qa_id=%s, type=%s", qa_id, type(value).__name__)
    return []


def sanitize_source_chunk(item: dict) -> QAHistorySourceChunk:
    return QAHistorySourceChunk(
        chunk_id=item.get("chunk_id"),
        document_id=item.get("document_id"),
        filename=item.get("filename"),
        source_id=item.get("source_id"),
        source_type=item.get("source_type"),
        source_name=item.get("source_name"),
        source_path=item.get("source_path"),
        heading_path=item.get("heading_path") or [],
        section_title=item.get("section_title"),
        line_start=item.get("line_start"),
        line_end=item.get("line_end"),
        tags=item.get("tags") or [],
        page_no=item.get("page_no"),
        chunk_index=item.get("chunk_index"),
        score=item.get("score"),
        rerank_score=item.get("rerank_score"),
        text=item.get("text"),
    )


def answer_preview(answer: str, limit: int = 180) -> str:
    normalized = " ".join((answer or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


@router.get("/history", response_model=QAHistoryListResponse)
def list_qa_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> QAHistoryListResponse:
    logger.info(
        "QA history list requested: page=%s, page_size=%s, document_id=%s, keyword=%s",
        page,
        page_size,
        document_id or "-",
        bool(keyword),
    )
    try:
        query = db.query(QAHistory)
        if keyword:
            pattern = f"%{keyword.strip()}%"
            query = query.filter(or_(QAHistory.question.ilike(pattern), QAHistory.answer.ilike(pattern)))
        if document_id:
            query = query.filter(cast(QAHistory.source_chunks, Text).contains(document_id.strip()))

        total = query.count()
        rows = (
            query.order_by(QAHistory.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
    except SQLAlchemyError as exc:
        logger.exception("Failed to list QA history")
        raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_DETAIL) from exc

    items = []
    for row in rows:
        sources = parse_source_chunks(row.source_chunks, row.id)
        items.append(
            QAHistoryItem(
                id=row.id,
                question=row.question,
                rewritten_query=row.rewritten_query,
                answer_preview=answer_preview(row.answer),
                created_at=row.created_at,
                source_count=len(sources),
            )
        )

    return QAHistoryListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/history/{qa_id}", response_model=QAHistoryDetailResponse)
def get_qa_history(qa_id: str, db: Session = Depends(get_db)) -> QAHistoryDetailResponse:
    logger.info("QA history detail requested: qa_id=%s", qa_id)
    try:
        history = db.get(QAHistory, qa_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get QA history %s", qa_id)
        raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_DETAIL) from exc

    if history is None:
        logger.info("QA history not found: qa_id=%s", qa_id)
        raise HTTPException(status_code=404, detail="QA history not found")

    source_chunks = [sanitize_source_chunk(item) for item in parse_source_chunks(history.source_chunks, history.id)]
    return QAHistoryDetailResponse(
        id=history.id,
        question=history.question,
        rewritten_query=history.rewritten_query,
        answer=history.answer,
        source_chunks=source_chunks,
        created_at=history.created_at,
    )
