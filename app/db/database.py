from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_document_lifecycle_columns()


def ensure_document_lifecycle_columns() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("documents"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("documents")}
    column_sql = {
        "updated_at": "ALTER TABLE documents ADD COLUMN updated_at DATETIME NULL",
        "processed_at": "ALTER TABLE documents ADD COLUMN processed_at DATETIME NULL",
        "deleted_at": "ALTER TABLE documents ADD COLUMN deleted_at DATETIME NULL",
        "purged_at": "ALTER TABLE documents ADD COLUMN purged_at DATETIME NULL",
        "file_hash": "ALTER TABLE documents ADD COLUMN file_hash VARCHAR(64) NULL",
        "error_message": "ALTER TABLE documents ADD COLUMN error_message TEXT NULL",
    }

    with engine.begin() as connection:
        for column_name, ddl in column_sql.items():
            if column_name not in existing_columns:
                connection.execute(text(ddl))
