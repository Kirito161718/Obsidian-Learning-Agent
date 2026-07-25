from functools import lru_cache
import os
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
    reranker_device: str = "auto"
    reranker_batch_size: int = Field(default=8, ge=1, le=64)

    rag_rewrite_enabled: bool = True
    rag_rewrite_score_threshold: float = 0.45
    rag_rewrite_min_results: int = 2
    rag_rewrite_short_query_length: int = 4
    rag_context_max_chars: int = 12000
    rag_max_chunks_per_source: int = 3
    rag_evaluation_enabled: bool = True
    rag_evaluation_data_dir: Path = Path("data/rag_evaluation")
    rag_evaluation_report_dir: Path = Path("reports/rag_evaluation")
    rag_evaluation_max_cases: int = Field(default=100, ge=1, le=10_000)
    rag_evaluation_default_top_k_values: str = "5,10,20"
    rag_evaluation_repeat_count: int = Field(default=1, ge=1, le=20)
    rag_evaluation_final_repeat_count: int = Field(default=3, ge=1, le=20)
    rag_evaluation_max_concurrent_cases: int = Field(default=1, ge=1, le=16)
    rag_evaluation_langsmith_enabled: bool = True
    rag_evaluation_langsmith_dataset: str = "obsidian-learning-agent-rag-eval-v1"
    rag_evaluation_langsmith_project: str = "obsidian-learning-agent"
    rag_evaluation_upload_safe_cases_only: bool = True
    rag_evaluation_collection_prefix: str = "rag_eval_"
    rag_evaluation_keep_collections: int = Field(default=3, ge=0, le=100)
    rag_evaluation_keep_experiments: int = Field(default=20, ge=1, le=1000)
    rag_promotion_min_recall_gain: float = Field(default=0.02, ge=0, le=1)
    rag_promotion_min_ndcg_gain: float = Field(default=0.02, ge=0, le=1)
    rag_promotion_max_citation_drop: float = Field(default=0.01, ge=0, le=1)
    rag_promotion_max_no_answer_drop: float = Field(default=0.02, ge=0, le=1)
    rag_promotion_max_p95_latency_increase_ratio: float = Field(default=0.20, ge=0, le=10)
    rag_retrieval_profile_version: str = "rag-baseline-v1"
    rag_model_prewarm_enabled: bool = False
    rag_model_prewarm_on_startup: bool = False
    model_prewarm_enabled: bool | None = None
    model_prewarm_on_startup: bool | None = None
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
        "get_session_messages,get_user_preferences,retrieve_knowledge,"
        "list_official_sources,get_update_proposal,preview_update_diff,apply_update_proposal"
    )

    mcp_enabled: bool = True
    mcp_config_path: Path = Path("config/mcp_servers.local.yaml")
    mcp_max_input_chars: int = Field(default=10000, ge=100, le=1_000_000)
    mcp_max_output_chars: int = Field(default=20000, ge=100, le=2_000_000)
    mcp_max_schema_chars: int = Field(default=50000, ge=1000, le=2_000_000)
    mcp_max_tools_per_server: int = Field(default=50, ge=1, le=500)
    mcp_default_connect_timeout_seconds: int = Field(default=15, ge=1, le=300)
    mcp_default_tool_timeout_seconds: int = Field(default=30, ge=1, le=600)
    mcp_stderr_preview_chars: int = Field(default=1000, ge=100, le=10000)

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

    official_sources_enabled: bool = True
    official_fetch_timeout_seconds: int = Field(default=20, ge=1, le=120)
    official_fetch_max_redirects: int = Field(default=3, ge=0, le=10)
    official_fetch_max_bytes: int = Field(default=10_485_760, ge=1024, le=100_000_000)
    official_fetch_user_agent: str = Field(default="Obsidian-Learning-Agent/1.0", min_length=1, max_length=200)
    official_fetch_allowed_content_types: str = (
        "text/html,text/plain,text/markdown,application/pdf"
    )
    official_fetch_allow_local_http: bool = False
    official_html_max_chars: int = Field(default=500_000, ge=1000, le=5_000_000)
    official_pdf_max_pages: int = Field(default=300, ge=1, le=1000)
    official_snapshot_keep: int = Field(default=5, ge=1, le=100)
    official_reminder_overdue_grace_days: int = Field(default=7, ge=1, le=365)
    official_reminder_batch_limit: int = Field(default=10, ge=1, le=10)
    note_version_keep: int = Field(default=10, ge=1, le=1000)
    update_proposal_retention_days: int = Field(default=30, ge=1, le=3650)
    official_audit_max_linked_notes: int = Field(default=10, ge=1, le=100)
    official_proposal_max_items: int = Field(default=10, ge=1, le=100)
    official_proposal_max_operations_per_note: int = Field(default=20, ge=1, le=200)
    official_proposal_max_change_ratio: float = Field(default=0.35, gt=0, le=1)
    official_evidence_excerpt_max_chars: int = Field(default=500, ge=20, le=5000)
    official_proposal_summary_max_chars: int = Field(default=2000, ge=100, le=20_000)
    approval_expires_hours: int = Field(default=24, ge=1, le=720)
    official_checkpoint_enabled: bool = True

    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    langsmith_tracing: bool = False
    langsmith_api_key: str = Field(default="", repr=False)
    langsmith_project: str = "obsidian-learning-agent"
    langsmith_workspace_id: str | None = Field(default=None, repr=False)
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_observability_enabled: bool | None = None
    langsmith_safe_mode: bool = True
    langsmith_hide_inputs: bool = True
    langsmith_hide_outputs: bool = True
    langsmith_trace_schema_version: str = "ola-trace-v1"
    langsmith_environment: str = "local"
    langsmith_flush_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    langsmith_max_metadata_chars: int = Field(default=10_000, ge=1000, le=100_000)
    langsmith_max_error_preview_chars: int = Field(default=500, ge=50, le=2000)
    langsmith_failure_cooldown_seconds: int = Field(default=60, ge=0, le=3600)

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("obsidian_vault_path", mode="before")
    @classmethod
    def empty_obsidian_path_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

    @field_validator("langsmith_workspace_id", mode="before")
    @classmethod
    def empty_workspace_id_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

    @field_validator("reranker_device", mode="before")
    @classmethod
    def validate_reranker_device(cls, value: object) -> str:
        normalized = str(value or "auto").strip().casefold()
        if normalized not in {"auto", "cpu", "cuda"}:
            raise ValueError("RERANKER_DEVICE must be auto, cpu, or cuda")
        return normalized

    @model_validator(mode="after")
    def resolve_project_paths(self) -> "Settings":
        if not self.upload_dir.is_absolute():
            self.upload_dir = BASE_DIR / self.upload_dir
        if not self.mcp_config_path.is_absolute():
            self.mcp_config_path = BASE_DIR / self.mcp_config_path
        if not self.rag_evaluation_data_dir.is_absolute():
            self.rag_evaluation_data_dir = BASE_DIR / self.rag_evaluation_data_dir
        if not self.rag_evaluation_report_dir.is_absolute():
            self.rag_evaluation_report_dir = BASE_DIR / self.rag_evaluation_report_dir
        if self.embedding_model:
            self.embedding_model_name = self.embedding_model
        if self.model_prewarm_enabled is not None:
            self.rag_model_prewarm_enabled = self.model_prewarm_enabled
        if self.model_prewarm_on_startup is not None:
            self.rag_model_prewarm_on_startup = self.model_prewarm_on_startup
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
    def official_temp_dir(self) -> Path:
        return self.temp_dir / "official_updates"

    @property
    def official_snapshot_dir(self) -> Path:
        return BASE_DIR / "data" / "official_sources" / "snapshots"

    @property
    def update_proposal_dir(self) -> Path:
        return BASE_DIR / "data" / "update_proposals"

    @property
    def note_version_dir(self) -> Path:
        return BASE_DIR / "data" / "note_versions"

    @property
    def report_dir(self) -> Path:
        return BASE_DIR / "reports"

    @property
    def model_cache_dir(self) -> Path:
        return BASE_DIR / "model_cache"

    @property
    def rag_evaluation_dataset_dir(self) -> Path:
        return self.rag_evaluation_data_dir / "datasets"

    @property
    def rag_evaluation_artifact_dir(self) -> Path:
        return self.rag_evaluation_data_dir / "experiment_artifacts"

    @property
    def rag_evaluation_top_k_values(self) -> tuple[int, ...]:
        values = tuple(
            sorted(
                {
                    int(item)
                    for item in self._csv_values(self.rag_evaluation_default_top_k_values)
                    if item.isdigit() and 1 <= int(item) <= 100
                }
            )
        )
        return values or (5, 10, 20)

    @property
    def langsmith_observability_requested(self) -> bool:
        compatibility_enabled = (
            self.langsmith_tracing
            if self.langsmith_observability_enabled is None
            else self.langsmith_observability_enabled
        )
        return bool(self.langsmith_tracing and compatibility_enabled)

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

    @property
    def official_fetch_allowed_content_type_list(self) -> tuple[str, ...]:
        return tuple(value.casefold() for value in self._csv_values(self.official_fetch_allowed_content_types))

    @staticmethod
    def _csv_values(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.langsmith_safe_mode:
        # Apply the SDK's first privacy layer as soon as settings are loaded.
        # The explicit Client configuration in app.observability applies the
        # same protection independently.
        os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = "true"
    return settings
