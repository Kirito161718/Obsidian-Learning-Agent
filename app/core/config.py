from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
    learning_session_review_max_qa: int = Field(default=20, ge=1, le=100)

    session_memory_enabled: bool = True
    session_memory_max_turns: int = Field(default=6, ge=1, le=50)
    session_memory_max_chars: int = Field(default=3000, ge=100, le=20000)
    session_memory_answer_preview_chars: int = Field(default=500, ge=20, le=5000)
    user_preferences_max_chars: int = Field(default=1000, ge=1, le=10000)
    explicit_learning_context_max_chars: int = Field(default=1200, ge=100, le=10000)
    rag_total_prompt_max_chars: int = Field(default=18000, ge=1000, le=100000)

    run_history_retention_days: int = Field(default=10, ge=1, le=3650)
    run_question_preview_chars: int = Field(default=200, ge=20, le=2000)
    run_error_preview_chars: int = Field(default=500, ge=20, le=5000)

    tool_default_timeout_seconds: int = Field(default=15, ge=1, le=300)
    tool_audit_input_max_chars: int = Field(default=1000, ge=50, le=10000)
    tool_audit_output_max_chars: int = Field(default=1000, ge=50, le=10000)
    enabled_internal_tools: str = (
        "list_learning_goals,list_learning_status,search_obsidian_notes,"
        "get_session_messages,get_user_preferences,retrieve_knowledge"
    )

    obsidian_enabled: bool = False
    obsidian_vault_path: Path | None = None
    obsidian_include_patterns: str = "*.md,**/*.md"
    obsidian_exclude_dirs: str = ".obsidian,.trash,Attachments,Templates,Archive,_ai_updates"
    obsidian_exclude_paths: str = "Learning/_drafts"
    obsidian_max_file_bytes: int = 2_097_152
    obsidian_chunk_min_chars: int = 200
    obsidian_chunk_target_chars: int = 1200
    obsidian_chunk_max_chars: int = 1800
    obsidian_atomic_block_hard_max_chars: int = 6000

    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("obsidian_vault_path", mode="before")
    @classmethod
    def empty_obsidian_path_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

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

    @property
    def obsidian_include_pattern_list(self) -> tuple[str, ...]:
        return self._csv_values(self.obsidian_include_patterns)

    @property
    def obsidian_exclude_dir_list(self) -> tuple[str, ...]:
        return self._csv_values(self.obsidian_exclude_dirs)

    @property
    def obsidian_exclude_path_list(self) -> tuple[str, ...]:
        return tuple(value.replace("\\", "/").strip("/") for value in self._csv_values(self.obsidian_exclude_paths))

    @property
    def enabled_internal_tool_list(self) -> tuple[str, ...]:
        return self._csv_values(self.enabled_internal_tools)

    @staticmethod
    def _csv_values(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
