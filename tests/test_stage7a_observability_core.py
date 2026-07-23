import asyncio
import json
import logging
from dataclasses import replace

import pytest

from app.core.config import Settings
from app.observability import reset_observability
from app.observability.configuration import ObservabilityConfiguration
from app.observability.errors import (
    OBSERVABILITY_DISABLED,
    OBSERVABILITY_EXPORT_FAILED,
    OBSERVABILITY_NOT_CONFIGURED,
)
from app.observability.manager import LangSmithCollector, ObservabilityManager
from app.observability.metadata import query_metadata
from app.observability.sanitization import sanitize_metadata, sanitize_tags
from app.observability.testing import FakeCollector


@pytest.fixture(autouse=True)
def reset_global_observability():
    reset_observability()
    yield
    reset_observability()


def configuration(**overrides) -> ObservabilityConfiguration:
    values = {
        "app_env": "development",
        "langsmith_tracing": True,
        "langsmith_observability_enabled": True,
        "langsmith_api_key": "test-key",
    }
    values.update(overrides)
    settings = Settings(_env_file=None, **values)
    return ObservabilityConfiguration.from_settings(
        settings,
        allow_test_environment=True,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_configuration_defaults_are_safe_and_disabled():
    settings = Settings(_env_file=None)
    config = ObservabilityConfiguration.from_settings(settings)
    assert config.enabled is False
    assert config.safe_mode is True
    assert config.hide_inputs is True
    assert config.hide_outputs is True
    assert config.initial_error_code == OBSERVABILITY_DISABLED


def test_langsmith_tracing_compatibility_enables_optional_adapter():
    config = configuration(langsmith_observability_enabled=None)
    assert config.requested is True and config.enabled is True and config.configured is True


def test_missing_key_is_not_configured_but_does_not_raise():
    config = ObservabilityConfiguration.from_settings(
        Settings(
            _env_file=None,
            app_env="development",
            langsmith_tracing=True,
            langsmith_observability_enabled=True,
            langsmith_api_key="",
        ),
        allow_test_environment=True,
    )
    assert config.enabled is False
    assert config.initial_error_code == OBSERVABILITY_NOT_CONFIGURED


def test_unsafe_hide_configuration_disables_export():
    config = configuration(langsmith_hide_inputs=False)
    assert config.enabled is False and config.configured is False


def test_workspace_id_is_optional_and_only_status_boolean_is_exposed():
    config = configuration(langsmith_workspace_id=None)
    status = ObservabilityManager(config, collector=FakeCollector()).status()
    assert status["workspace_configured"] is False
    assert "workspace_id" not in status and "api_key" not in status and "endpoint" not in status


def test_disabled_manager_returns_noop_span():
    config = replace(configuration(), enabled=False, initial_status="disabled")
    manager = ObservabilityManager(config, collector=FakeCollector())
    with manager.trace(name="disabled") as span:
        assert span.enabled is False
        assert span.external_trace_id is None


def test_sync_parent_child_context_is_preserved():
    collector = FakeCollector()
    manager = ObservabilityManager(configuration(), collector=collector)
    with manager.trace(name="parent", metadata={"internal_run_id": "run-1"}):
        with manager.trace(name="child"):
            pass
    assert len(collector.spans) == 2
    assert collector.spans[1].parent_run_id == collector.spans[0].id
    assert collector.spans[1].trace_id == collector.spans[0].trace_id


@pytest.mark.anyio
async def test_async_parent_child_context_is_preserved():
    collector = FakeCollector()
    manager = ObservabilityManager(configuration(), collector=collector)
    async with manager.trace_async(name="async-parent"):
        await asyncio.sleep(0)
        async with manager.trace_async(name="async-child"):
            await asyncio.sleep(0)
    assert collector.spans[1].parent_run_id == collector.spans[0].id


def test_business_exception_is_re_raised_without_traceback_metadata():
    collector = FakeCollector()
    manager = ObservabilityManager(configuration(), collector=collector)
    with pytest.raises(ValueError, match="business secret"):
        with manager.trace(name="failure"):
            raise ValueError("business secret")
    serialized = json.dumps(collector.spans[0].metadata)
    assert "business secret" not in serialized
    assert "Traceback" not in serialized
    assert collector.spans[0].metadata["error_type"] == "ValueError"


def test_collector_open_failure_is_fail_open():
    manager = ObservabilityManager(configuration(), collector=FakeCollector(fail_open=True))
    completed = False
    with manager.trace(name="unavailable") as span:
        completed = True
        assert span.enabled is False
    assert completed is True
    status = manager.status()
    assert status["status"] == "failed"
    assert status["last_error_code"] == OBSERVABILITY_EXPORT_FAILED


def test_flush_success_and_failure_are_bounded():
    collector = FakeCollector()
    manager = ObservabilityManager(configuration(), collector=collector)
    assert manager.flush(0.1) is True and collector.flush_count == 1
    collector.fail_flush = True
    assert manager.flush(0.1) is False
    assert manager.status()["dropped_or_failed_count"] == 1


def test_langsmith_sdk_diagnostics_are_suppressed(caplog):
    LangSmithCollector._install_safe_sdk_logging()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("langsmith.client").warning(
            "API Key: masked-key trace=private-trace-id"
        )
    assert "masked-key" not in caplog.text
    assert "private-trace-id" not in caplog.text


def test_metadata_sanitizer_blocks_all_private_payload_categories():
    secret = "private-query user@example.com +86 138-0013-8000"
    metadata = sanitize_metadata(
        {
            "question": secret,
            "answer": secret,
            "prompt": secret,
            "memory": [secret],
            "chunks": [secret],
            "snapshot": secret,
            "proposal": secret,
            "diff": secret,
            "messages": [secret],
            "token": "secret-token",
            "api_key": "secret-key",
            "model_name": r"C:\Users\private\vault\note.md",
            "project_name": "/home/private/vault/note.md",
            "provider_name": secret,
            "traceback": "Traceback (most recent call last): private",
        },
        10_000,
    )
    serialized = json.dumps(metadata, ensure_ascii=False)
    assert "private-query" not in serialized
    assert "user@example.com" not in serialized
    assert "138-0013-8000" not in serialized
    assert "secret-token" not in serialized
    assert "secret-key" not in serialized
    assert r"C:\Users" not in serialized and "Traceback (most recent" not in serialized
    assert "/home/private/vault/note.md" not in serialized
    for key in ("question_redacted", "answer_redacted", "prompt_redacted", "memory_redacted"):
        assert metadata[key] is True


def test_metadata_size_and_tag_limits_are_enforced():
    metadata = sanitize_metadata(
        {f"safe_{index}_name": "x" * 300 for index in range(100)},
        1000,
    )
    assert metadata["metadata_truncated"] is True
    assert len(json.dumps(metadata, ensure_ascii=False)) <= 1100
    tags = sanitize_tags([f"tag {index}" for index in range(100)])
    assert len(tags) == 20
    assert all(len(tag) <= 80 and " " not in tag for tag in tags)


def test_query_metadata_detects_reference_without_preserving_query():
    query = "它和前面的内容有什么关系？"
    metadata = query_metadata(query)
    assert metadata["has_reference_expression"] is True
    assert metadata["query_language"] == "zh"
    assert query not in json.dumps(metadata, ensure_ascii=False)


def test_status_never_exposes_secret_configuration():
    status = ObservabilityManager(configuration(), collector=FakeCollector()).status()
    serialized = json.dumps(status)
    assert "test-key" not in serialized
    assert "api.smith" not in serialized
    assert "api_key" not in serialized
