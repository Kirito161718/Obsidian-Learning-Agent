from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_

from app.core.config import Settings
from app.db.models import LearningGoal, LearningStatus, ObsidianNote, QAHistory
from app.schemas.agent import UserPreferenceOut
from app.schemas.goal import GoalStatus, GoalType
from app.schemas.learning_status import LearningStatusValue
from app.schemas.rag import RetrieveRequest
from app.security.redaction import safe_text_preview
from app.services.importance_service import ImportanceService
from app.services.learning_session_service import LearningSessionService
from app.services.user_preference_service import UserPreferenceService
from app.tools.definitions import CallerType, RiskLevel, ToolDefinition
from app.tools.executor import ToolExecutionContext


class GoalListInput(BaseModel):
    status: GoalStatus | None = None
    goal_type: GoalType | None = None
    limit: int = Field(default=20, ge=1, le=100)


class GoalItem(BaseModel):
    id: str
    name: str
    goal_type: str
    priority: int
    status: str
    related_topics: list[str]


class GoalListOutput(BaseModel):
    total: int
    items: list[GoalItem]


class StatusListInput(BaseModel):
    status: LearningStatusValue | None = None
    min_mastery: int | None = Field(default=None, ge=0, le=5)
    max_mastery: int | None = Field(default=None, ge=0, le=5)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.min_mastery is not None and self.max_mastery is not None and self.min_mastery > self.max_mastery:
            raise ValueError("min_mastery must not exceed max_mastery")
        return self


class StatusItem(BaseModel):
    id: str
    topic: str
    status: str
    mastery_level: int
    manual_importance: int
    importance_score: float
    importance_stars: int


class StatusListOutput(BaseModel):
    total: int
    items: list[StatusItem]


def _safe_relative(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    normalized = raw.replace("\\", "/").strip("/")
    first = normalized.split("/", 1)[0]
    if raw.startswith(("/", "\\")) or ":" in first or ".." in normalized.split("/"):
        raise ValueError("path must be Vault-relative")
    return normalized


class ObsidianSearchInput(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    path_prefix: str | None = None
    tag: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("path_prefix")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return _safe_relative(value)


class ObsidianNoteItem(BaseModel):
    id: str
    relative_path: str
    file_name: str
    title: str | None
    tags: list[str]
    index_status: str
    chunk_count: int


class ObsidianSearchOutput(BaseModel):
    total: int
    items: list[ObsidianNoteItem]


class SessionMessagesInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    limit: int = Field(default=20, ge=1, le=100)


class SessionMessageItem(BaseModel):
    id: str
    question: str
    answer_preview: str
    no_answer: bool
    created_at: datetime


class SessionMessagesOutput(BaseModel):
    session_id: str
    total: int
    items: list[SessionMessageItem]


class EmptyInput(BaseModel):
    pass


class PreferenceOutput(UserPreferenceOut):
    pass


class RetrieveKnowledgeInput(BaseModel):
    question: str = Field(min_length=1)
    source_types: list[Literal["uploaded_document", "obsidian_note"]] | None = None
    source_path_prefixes: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("source_path_prefixes")
    @classmethod
    def validate_paths(cls, value: list[str] | None) -> list[str] | None:
        return [_safe_relative(item) for item in value] if value else None


class KnowledgeChunkItem(BaseModel):
    chunk_id: str | None
    source_type: str | None
    source_name: str | None
    source_path: str | None
    section_title: str | None
    score: float
    text_preview: str


class RetrieveKnowledgeOutput(BaseModel):
    question: str
    rewritten_query: str
    rewrite_applied: bool
    retrieved_count: int
    run_id: str | None
    chunks: list[KnowledgeChunkItem]


def list_learning_goals(payload: GoalListInput, context: ToolExecutionContext) -> dict:
    query = context.db.query(LearningGoal)
    if payload.status:
        query = query.filter(LearningGoal.status == payload.status.value)
    if payload.goal_type:
        query = query.filter(LearningGoal.goal_type == payload.goal_type.value)
    rows = query.order_by(LearningGoal.priority.desc(), LearningGoal.updated_at.desc()).limit(payload.limit).all()
    items = [
        {
            "id": row.id,
            "name": row.name,
            "goal_type": row.goal_type,
            "priority": row.priority,
            "status": row.status,
            "related_topics": list(row.related_topics or [])[:20],
        }
        for row in rows
    ]
    return {"total": len(items), "items": items}


def list_learning_status(payload: StatusListInput, context: ToolExecutionContext) -> dict:
    query = context.db.query(LearningStatus)
    if payload.status:
        query = query.filter(LearningStatus.status == payload.status.value)
    if payload.min_mastery is not None:
        query = query.filter(LearningStatus.mastery_level >= payload.min_mastery)
    if payload.max_mastery is not None:
        query = query.filter(LearningStatus.mastery_level <= payload.max_mastery)
    rows = query.order_by(LearningStatus.updated_at.desc()).limit(payload.limit).all()
    goal_ids = {goal_id for row in rows for goal_id in (row.related_goal_ids or [])}
    goals = context.db.query(LearningGoal).filter(LearningGoal.id.in_(goal_ids)).all() if goal_ids else []
    goals_by_id = {goal.id: goal for goal in goals}
    importance = ImportanceService()
    items = []
    for row in rows:
        score = importance.calculate(row, goals_by_id)
        items.append(
            {
                "id": row.id,
                "topic": row.topic,
                "status": row.status,
                "mastery_level": row.mastery_level,
                "manual_importance": row.manual_importance,
                "importance_score": score.importance_score,
                "importance_stars": score.importance_stars,
            }
        )
    return {"total": len(items), "items": items}


def search_obsidian_notes(payload: ObsidianSearchInput, context: ToolExecutionContext) -> dict:
    query = context.db.query(ObsidianNote)
    if payload.query:
        pattern = f"%{payload.query.strip()}%"
        query = query.filter(
            or_(ObsidianNote.title.ilike(pattern), ObsidianNote.file_name.ilike(pattern), ObsidianNote.relative_path.ilike(pattern))
        )
    if payload.path_prefix:
        query = query.filter(ObsidianNote.relative_path.like(f"{payload.path_prefix}%"))
    rows = query.order_by(ObsidianNote.relative_path).limit(payload.limit * 3).all()
    if payload.tag:
        rows = [row for row in rows if payload.tag in (row.tags or [])]
    rows = rows[: payload.limit]
    items = [
        {
            "id": row.id,
            "relative_path": row.relative_path.replace("\\", "/"),
            "file_name": row.file_name,
            "title": row.title,
            "tags": list(row.tags or [])[:30],
            "index_status": row.index_status,
            "chunk_count": row.chunk_count,
        }
        for row in rows
    ]
    return {"total": len(items), "items": items}


def get_session_messages(payload: SessionMessagesInput, context: ToolExecutionContext) -> dict:
    LearningSessionService().get(context.db, payload.session_id)
    rows = (
        context.db.query(QAHistory)
        .filter(QAHistory.session_id == payload.session_id)
        .order_by(QAHistory.created_at.desc())
        .limit(payload.limit)
        .all()
    )
    rows.reverse()
    items = [
        {
            "id": row.id,
            "question": safe_text_preview(row.question, 500),
            "answer_preview": safe_text_preview(row.answer, 500),
            "no_answer": not bool(row.source_chunks),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return {"session_id": payload.session_id, "total": len(items), "items": items}


def get_user_preferences(payload: EmptyInput, context: ToolExecutionContext) -> dict:
    return UserPreferenceService().get(context.db)


def _get_rag_service():
    from app.routers.rag import get_rag_service

    return get_rag_service()


def retrieve_knowledge(payload: RetrieveKnowledgeInput, context: ToolExecutionContext) -> dict:
    service = context.services.get("rag_service") or _get_rag_service()
    response = service.retrieve(
        RetrieveRequest(
            question=payload.question,
            source_types=payload.source_types,
            source_path_prefixes=payload.source_path_prefixes,
            top_k=payload.top_k,
        ),
        context.db,
    )
    chunks = [
        {
            "chunk_id": item.chunk_id,
            "source_type": item.source_type,
            "source_name": item.source_name,
            "source_path": item.source_path.replace("\\", "/") if item.source_path else None,
            "section_title": item.section_title,
            "score": item.score,
            "text_preview": safe_text_preview(item.text, 800),
        }
        for item in response.chunks[: payload.top_k]
    ]
    return {
        "question": response.question,
        "rewritten_query": response.rewritten_query,
        "rewrite_applied": response.rewrite_applied,
        "retrieved_count": len(chunks),
        "run_id": response.run_id,
        "chunks": chunks,
    }


def internal_tool_definitions(settings: Settings) -> list[ToolDefinition]:
    enabled = set(settings.enabled_internal_tool_list)
    callers = frozenset({CallerType.user})
    specs: list[tuple[str, str, type[BaseModel], type[BaseModel], Any]] = [
        ("list_learning_goals", "列出手动维护的学习目标。", GoalListInput, GoalListOutput, list_learning_goals),
        ("list_learning_status", "列出学习状态及确定性重要指数。", StatusListInput, StatusListOutput, list_learning_status),
        ("search_obsidian_notes", "按标题、相对路径前缀或标签搜索 Obsidian 笔记元数据。", ObsidianSearchInput, ObsidianSearchOutput, search_obsidian_notes),
        ("get_session_messages", "读取指定学习 Session 的最近问答预览。", SessionMessagesInput, SessionMessagesOutput, get_session_messages),
        ("get_user_preferences", "读取当前用户手动偏好。", EmptyInput, PreferenceOutput, get_user_preferences),
        ("retrieve_knowledge", "复用现有 RAG Retrieve 检索知识，不生成答案。", RetrieveKnowledgeInput, RetrieveKnowledgeOutput, retrieve_knowledge),
    ]
    return [
        ToolDefinition(
            name=name,
            description=description,
            risk_level=RiskLevel.READ_ONLY,
            input_schema=input_schema,
            output_schema=output_schema,
            callable=callable_obj,
            enabled=name in enabled,
            idempotent=True,
            timeout_seconds=settings.tool_default_timeout_seconds,
            allowed_callers=callers,
        )
        for name, description, input_schema, output_schema, callable_obj in specs
    ]
