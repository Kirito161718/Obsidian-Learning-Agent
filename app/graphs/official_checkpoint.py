from __future__ import annotations

from collections.abc import Iterator, Sequence
import threading
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import OfficialCheckpoint, OfficialCheckpointWrite
from app.official_sources.errors import official_error


class MySQLCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpoint adapter backed by the application's SQLAlchemy engine.

    The production engine is MySQL/PyMySQL. Tests bind the same implementation to
    SQLite, so restart semantics can be exercised without introducing a second
    persistence design.
    """

    def __init__(self, engine: Engine, *, serde=None) -> None:
        super().__init__(serde=serde)
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._lock = threading.RLock()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config.get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
        if not thread_id:
            raise official_error("official_checkpoint_unavailable", "Checkpoint thread_id is required", status_code=503)
        checkpoint_id = get_checkpoint_id(config)
        try:
            with self._lock:
                with self._session_factory() as session:
                    statement = select(OfficialCheckpoint).where(
                        OfficialCheckpoint.thread_id == thread_id,
                        OfficialCheckpoint.checkpoint_ns == checkpoint_ns,
                    )
                    if checkpoint_id:
                        statement = statement.where(OfficialCheckpoint.checkpoint_id == checkpoint_id)
                    else:
                        statement = statement.order_by(OfficialCheckpoint.checkpoint_id.desc()).limit(1)
                    row = session.scalar(statement)
                    return self._tuple(session, row) if row else None
        except SQLAlchemyError as exc:
            raise official_error("official_checkpoint_unavailable", "Persistent checkpoint is unavailable", status_code=503) from exc

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        try:
            with self._session_factory() as session:
                statement = select(OfficialCheckpoint)
                if config:
                    configurable = config.get("configurable") or {}
                    if thread_id := configurable.get("thread_id"):
                        statement = statement.where(OfficialCheckpoint.thread_id == str(thread_id))
                    if "checkpoint_ns" in configurable:
                        statement = statement.where(
                            OfficialCheckpoint.checkpoint_ns == str(configurable.get("checkpoint_ns") or "")
                        )
                    if checkpoint_id := get_checkpoint_id(config):
                        statement = statement.where(OfficialCheckpoint.checkpoint_id == checkpoint_id)
                if before and (before_id := get_checkpoint_id(before)):
                    statement = statement.where(OfficialCheckpoint.checkpoint_id < before_id)
                statement = statement.order_by(OfficialCheckpoint.checkpoint_id.desc())
                rows = list(session.scalars(statement).all())
                yielded = 0
                for row in rows:
                    item = self._tuple(session, row)
                    if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                        continue
                    yield item
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        break
        except SQLAlchemyError as exc:
            raise official_error("official_checkpoint_unavailable", "Persistent checkpoint is unavailable", status_code=503) from exc

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions
        configurable = config.get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
        if not thread_id:
            raise official_error("official_checkpoint_unavailable", "Checkpoint thread_id is required", status_code=503)
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(dict(checkpoint))
        metadata_type, metadata_blob = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        row = OfficialCheckpoint(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=str(checkpoint["id"]),
            parent_checkpoint_id=str(configurable.get("checkpoint_id")) if configurable.get("checkpoint_id") else None,
            checkpoint_type=checkpoint_type,
            checkpoint_blob=checkpoint_blob,
            metadata_type=metadata_type,
            metadata_blob=metadata_blob,
        )
        try:
            with self._lock:
                with self._session_factory.begin() as session:
                    session.merge(row)
        except SQLAlchemyError as exc:
            raise official_error("official_checkpoint_unavailable", "Persistent checkpoint could not be saved", status_code=503) from exc
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": str(checkpoint["id"]),
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config.get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
        checkpoint_id = str(configurable.get("checkpoint_id") or "")
        if not thread_id or not checkpoint_id:
            raise official_error("official_checkpoint_unavailable", "Checkpoint write identity is incomplete", status_code=503)
        try:
            with self._lock:
                with self._session_factory.begin() as session:
                    for index, (channel, value) in enumerate(writes):
                        write_index = WRITES_IDX_MAP.get(channel, index)
                        key = (thread_id, checkpoint_ns, checkpoint_id, task_id, write_index)
                        if write_index >= 0 and session.get(OfficialCheckpointWrite, key) is not None:
                            continue
                        value_type, value_blob = self.serde.dumps_typed(value)
                        session.merge(
                            OfficialCheckpointWrite(
                                thread_id=thread_id,
                                checkpoint_ns=checkpoint_ns,
                                checkpoint_id=checkpoint_id,
                                task_id=task_id,
                                write_index=write_index,
                                channel=channel,
                                value_type=value_type,
                                value_blob=value_blob,
                                task_path=task_path,
                            )
                        )
        except (IntegrityError, SQLAlchemyError) as exc:
            raise official_error("official_checkpoint_unavailable", "Checkpoint writes could not be saved", status_code=503) from exc

    def delete_thread(self, thread_id: str) -> None:
        try:
            with self._lock:
                with self._session_factory.begin() as session:
                    session.execute(delete(OfficialCheckpointWrite).where(OfficialCheckpointWrite.thread_id == thread_id))
                    session.execute(delete(OfficialCheckpoint).where(OfficialCheckpoint.thread_id == thread_id))
        except SQLAlchemyError as exc:
            raise official_error("official_checkpoint_unavailable", "Checkpoint thread could not be deleted", status_code=503) from exc

    def _tuple(self, session: Session, row: OfficialCheckpoint) -> CheckpointTuple:
        writes = list(
            session.scalars(
                select(OfficialCheckpointWrite)
                .where(
                    OfficialCheckpointWrite.thread_id == row.thread_id,
                    OfficialCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                    OfficialCheckpointWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(
                    OfficialCheckpointWrite.task_path,
                    OfficialCheckpointWrite.task_id,
                    OfficialCheckpointWrite.write_index,
                )
            ).all()
        )
        config: RunnableConfig = {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.checkpoint_ns,
                "checkpoint_id": row.checkpoint_id,
            }
        }
        parent = (
            {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
            if row.parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            config=config,
            checkpoint=self.serde.loads_typed((row.checkpoint_type, bytes(row.checkpoint_blob))),
            metadata=self.serde.loads_typed((row.metadata_type, bytes(row.metadata_blob))),
            parent_config=parent,
            pending_writes=[
                (item.task_id, item.channel, self.serde.loads_typed((item.value_type, bytes(item.value_blob))))
                for item in writes
            ],
        )
