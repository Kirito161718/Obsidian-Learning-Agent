from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "rag-knowledge-base"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    admin_enabled: bool = True
    database_required_on_startup: bool = False
    upload_dir: Path = Path("uploads")
    max_upload_size_mb: int = 50

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "rag_knowledge_base"
    database_url_value: str | None = Field(default=None, validation_alias="DATABASE_URL", repr=False)

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "rag_chunks"
    qdrant_prefer_grpc: bool = True
    qdrant_grpc_port: int = 6334

    embedding_model_name: str = "BAAI/bge-m3"
    embedding_model: str | None = Field(default=None, validation_alias="EMBEDDING_MODEL")
    embedding_device: str = "cpu"
    embedding_batch_size: int = 8

    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 5

    rag_rewrite_enabled: bool = True
    rag_rewrite_score_threshold: float = 0.45
    rag_rewrite_min_results: int = 2
    rag_rewrite_short_query_length: int = 4
    rag_context_max_chars: int = 12000
    rag_max_chunks_per_source: int = 3

    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def resolve_project_paths(self) -> "Settings":
        if not self.upload_dir.is_absolute():
            self.upload_dir = BASE_DIR / self.upload_dir
        if self.embedding_model:
            self.embedding_model_name = self.embedding_model
        return self

    @property
    def template_dir(self) -> Path:
        return BASE_DIR / "app" / "templates"

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "app" / "static"

    @property
    def runtime_dir(self) -> Path:
        return BASE_DIR / "runtime"

    @property
    def log_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.runtime_dir / "cache"

    @property
    def temp_dir(self) -> Path:
        return self.runtime_dir / "temp"

    @property
    def report_dir(self) -> Path:
        return BASE_DIR / "reports"

    @property
    def model_cache_dir(self) -> Path:
        return BASE_DIR / "model_cache"

    @property
    def database_url(self) -> URL:
        if self.database_url_value:
            return make_url(self.database_url_value)
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
