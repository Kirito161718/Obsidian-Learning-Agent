import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, get_settings
from app.db.database import SessionLocal, get_db
from app.db.models import Document, utc_now
from app.schemas.document import (
    DocumentChunk,
    DocumentChunksResponse,
    DocumentLifecycleResponse,
    DocumentOut,
    DocumentUploadResponse,
)
from app.services.chunk_service import ChunkService
from app.services.embedding_service import get_embedding_service
from app.services.parser_service import ParserService
from app.services.vector_service import VectorService
from app.utils.file_utils import calculate_file_sha256, save_upload_file, validate_upload_file

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)
DATABASE_UNAVAILABLE_DETAIL = "数据库暂时不可用，请确认 MySQL 已启动并且 .env 中的连接配置正确。"


def database_unavailable_detail() -> str:
    settings = get_settings()
    if not settings.mysql_password:
        return (
            "数据库暂时不可用：当前 MYSQL_PASSWORD 为空。"
            "请在项目根目录创建 .env，并填写正确的 MYSQL_HOST、MYSQL_USER、MYSQL_PASSWORD 和 MYSQL_DATABASE。"
        )
    return DATABASE_UNAVAILABLE_DETAIL


def qdrant_unavailable_detail() -> str:
    return "向量库暂时不可用，请确认 Qdrant 已启动，并且 document_id 对应的向量删除/写入可正常执行。"


def process_document(
    document_id: str,
    saved_path: Path | None = None,
    file_type: str | None = None,
    db: Session | None = None,
) -> None:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        document = session.get(Document, document_id)
        if document is None:
            logger.warning("Document %s not found during background processing", document_id)
            return
        if document.status in {"deleted", "purged"}:
            logger.info("Skip processing inactive document %s with status=%s", document_id, document.status)
            return

        source_path = saved_path or Path(document.file_path)
        source_type = file_type or document.file_type
        parser_name = ParserService.__name__

        try:
            logger.info(
                "Document parsing started: document_id=%s, filename=%s, parser=%s, file_type=%s",
                document.id,
                document.filename,
                parser_name,
                source_type,
            )
            sections = ParserService().parse(source_path, source_type)
            logger.info(
                "Document parsing completed: document_id=%s, filename=%s, parser=%s, section_count=%s",
                document.id,
                document.filename,
                parser_name,
                len(sections),
            )
            chunks = ChunkService().split(sections)
            logger.info("Document chunking completed: document_id=%s, chunk_count=%s", document.id, len(chunks))
            embedding_service = get_embedding_service()
            embeddings = embedding_service.embed_texts([chunk.text for chunk in chunks])
            vector_service = VectorService(vector_size=embedding_service.vector_size)
            vector_service.upsert_chunks(
                document_id=document.id,
                filename=document.filename,
                file_type=document.file_type,
                chunks=chunks,
                embeddings=embeddings,
            )
            logger.info("Qdrant upsert completed: document_id=%s, point_count=%s", document.id, len(chunks))
            document.chunk_count = len(chunks)
            document.status = "completed"
            document.processed_at = utc_now()
            document.updated_at = utc_now()
            document.error_message = None
            session.commit()
        except Exception as exc:
            document.status = "failed"
            document.updated_at = utc_now()
            document.error_message = str(exc)
            session.commit()
            logger.exception(
                "Document processing failed: document_id=%s, filename=%s, parser=%s, error=%s",
                document_id,
                document.filename,
                parser_name,
                str(exc)[:300],
            )
    finally:
        if owns_session:
            session.close()


def delete_document_vectors(document_id: str) -> None:
    vector_service = VectorService(ensure_collection=False)
    vector_service.delete_by_document_id(document_id)


def delete_document_file(file_path: str) -> bool:
    if not file_path:
        return False

    target = Path(file_path)
    if not target.is_absolute():
        target = BASE_DIR / target

    resolved_target = target.resolve()
    upload_dir = get_settings().upload_dir.resolve()
    if resolved_target == upload_dir or upload_dir not in resolved_target.parents:
        logger.warning("Skip deleting file outside upload directory: %s", file_path)
        return False

    if not resolved_target.exists() or not resolved_target.is_file():
        return False

    resolved_target.unlink()
    return True


@router.get("", response_model=list[DocumentOut])
def list_documents(
    status: str | None = Query(default=None, description="Filter by document status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[Document]:
    try:
        query = db.query(Document)
        if status:
            query = query.filter(Document.status == status)
        else:
            query = query.filter(Document.status.notin_(["deleted", "purged"]))
        return query.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    except SQLAlchemyError as exc:
        logger.exception("Failed to list documents")
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)) -> Document:
    try:
        document = db.get(Document, document_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get document %s", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}/chunks", response_model=DocumentChunksResponse)
def list_document_chunks(document_id: str, db: Session = Depends(get_db)) -> DocumentChunksResponse:
    logger.info("Document chunks requested: document_id=%s", document_id)
    try:
        document = db.get(Document, document_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get document %s before listing chunks", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.status in {"deleted", "purged"}:
        raise HTTPException(status_code=400, detail="Inactive document chunks are not available")

    try:
        payloads = VectorService(ensure_collection=False).list_chunks_by_document_id(document_id)
    except Exception as exc:
        logger.exception("Failed to list chunks from Qdrant for document %s", document_id)
        raise HTTPException(status_code=503, detail=qdrant_unavailable_detail()) from exc

    if document.chunk_count != len(payloads):
        logger.warning(
            "Document chunk count mismatch: document_id=%s, mysql_chunk_count=%s, qdrant_chunk_count=%s",
            document_id,
            document.chunk_count,
            len(payloads),
        )

    return DocumentChunksResponse(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        chunk_count=document.chunk_count,
        chunks=[
            DocumentChunk(
                chunk_id=payload.get("chunk_id"),
                chunk_index=int(payload.get("chunk_index", 0)),
                page_no=payload.get("page_no"),
                section_title=payload.get("section_title"),
                text=payload.get("text", ""),
            )
            for payload in payloads
        ],
    )


@router.delete("/{document_id}", response_model=DocumentLifecycleResponse)
def delete_document(document_id: str, db: Session = Depends(get_db)) -> DocumentLifecycleResponse:
    try:
        document = db.get(Document, document_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get document %s before delete", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        delete_document_vectors(document_id)
    except Exception as exc:
        logger.exception("Failed to delete vectors for document %s", document_id)
        raise HTTPException(status_code=503, detail=qdrant_unavailable_detail()) from exc

    try:
        document.status = "deleted"
        document.deleted_at = utc_now()
        document.updated_at = utc_now()
        document.error_message = None
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to mark document %s as deleted", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    logger.info("Document %s soft deleted", document_id)
    return DocumentLifecycleResponse(
        document_id=document_id,
        status="deleted",
        message="document soft deleted successfully",
    )


@router.delete("/{document_id}/purge", response_model=DocumentLifecycleResponse)
def purge_document(document_id: str, db: Session = Depends(get_db)) -> DocumentLifecycleResponse:
    try:
        document = db.get(Document, document_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get document %s before purge", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        delete_document_vectors(document_id)
    except Exception as exc:
        logger.exception("Failed to delete vectors for document %s during purge", document_id)
        raise HTTPException(status_code=503, detail=qdrant_unavailable_detail()) from exc

    file_deleted = delete_document_file(document.file_path)
    if not file_deleted:
        logger.warning("Purge did not delete source file: document_id=%s, file_path=%s", document_id, document.file_path)

    try:
        document.status = "purged"
        document.purged_at = utc_now()
        document.updated_at = utc_now()
        document.error_message = None
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to mark document %s as purged", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    logger.info("Document %s purged, file_deleted=%s", document_id, file_deleted)
    return DocumentLifecycleResponse(
        document_id=document_id,
        status="purged",
        message="document purged successfully",
    )


@router.post("/{document_id}/reindex", response_model=DocumentLifecycleResponse)
def reindex_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> DocumentLifecycleResponse:
    try:
        document = db.get(Document, document_id)
    except SQLAlchemyError as exc:
        logger.exception("Failed to get document %s before reindex", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.status in {"deleted", "purged"}:
        raise HTTPException(status_code=400, detail="Deleted document cannot be reindexed")

    try:
        delete_document_vectors(document_id)
    except Exception as exc:
        logger.exception("Failed to delete old vectors for document %s before reindex", document_id)
        raise HTTPException(status_code=503, detail=qdrant_unavailable_detail()) from exc

    try:
        document.status = "processing"
        document.chunk_count = 0
        document.processed_at = None
        document.updated_at = utc_now()
        document.error_message = None
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to mark document %s as processing", document_id)
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if get_settings().app_env == "test":
        background_tasks.add_task(process_document, document.id, db=db)
    else:
        background_tasks.add_task(process_document, document.id)

    logger.info("Document %s reindex started", document_id)
    return DocumentLifecycleResponse(
        document_id=document_id,
        status="processing",
        message="document reindex started",
    )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentUploadResponse:
    settings = get_settings()
    file_type = validate_upload_file(file)
    logger.info("Upload received: filename=%s, file_type=%s", file.filename, file_type)
    saved_path = await save_upload_file(file, settings.upload_dir, settings.max_upload_size_bytes)
    file_size = saved_path.stat().st_size if saved_path.exists() else None
    file_hash = calculate_file_sha256(saved_path)

    try:
        duplicate = (
            db.query(Document)
            .filter(Document.file_hash == file_hash, Document.status.notin_(["deleted", "purged"]))
            .order_by(Document.created_at.desc())
            .first()
        )
    except SQLAlchemyError as exc:
        saved_path.unlink(missing_ok=True)
        logger.exception("Failed to check duplicate upload by file_hash")
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if duplicate is not None:
        saved_path.unlink(missing_ok=True)
        logger.info("Duplicate upload detected: existing_document_id=%s, filename=%s", duplicate.id, duplicate.filename)
        return DocumentUploadResponse(
            document_id=duplicate.id,
            filename=duplicate.filename,
            status="duplicate",
            chunk_count=duplicate.chunk_count,
            file_hash=file_hash,
            message="same file already exists",
        )

    document = Document(
        filename=file.filename or saved_path.name,
        file_type=file_type,
        file_path=str(saved_path),
        file_hash=file_hash,
        status="processing",
    )
    try:
        db.add(document)
        db.commit()
        db.refresh(document)
        logger.info(
            "Upload metadata created: document_id=%s, filename=%s, file_type=%s, file_size=%s",
            document.id,
            document.filename,
            document.file_type,
            file_size,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        saved_path.unlink(missing_ok=True)
        logger.exception("Failed to create document metadata")
        raise HTTPException(status_code=503, detail=database_unavailable_detail()) from exc

    if settings.app_env == "test":
        background_tasks.add_task(process_document, document.id, saved_path, file_type, db)
    else:
        background_tasks.add_task(process_document, document.id, saved_path, file_type)

    return DocumentUploadResponse(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        chunk_count=document.chunk_count,
        file_hash=document.file_hash,
    )
