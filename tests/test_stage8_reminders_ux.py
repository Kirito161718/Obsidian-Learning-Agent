from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.models import OfficialSource, RagRetrievalProfile
from app.services.official_reminder_service import OfficialReminderService


def source(
    *,
    name: str = "Official Docs",
    enabled: bool = True,
    reminder_enabled: bool = True,
    interval: int = 30,
    checked_at: datetime | None = None,
    error: str | None = None,
) -> OfficialSource:
    return OfficialSource(
        name=name,
        url="https://example.com/docs",
        canonical_url="https://example.com/docs",
        allowed_host="example.com",
        source_type="html",
        enabled=enabled,
        status="active" if enabled else "disabled",
        reminder_enabled=reminder_enabled,
        check_interval_days=interval,
        last_checked_at=checked_at,
        last_error_code=error,
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"reminder_enabled": False}, "disabled"),
        ({"enabled": False}, "disabled"),
        ({"checked_at": None}, "never_checked"),
        ({"checked_at": datetime(2026, 7, 10, tzinfo=UTC)}, "current"),
        ({"checked_at": datetime(2026, 6, 25, tzinfo=UTC)}, "due"),
        ({"checked_at": datetime(2026, 6, 17, tzinfo=UTC)}, "overdue"),
        (
            {
                "checked_at": datetime(2026, 7, 24, tzinfo=UTC),
                "error": "official_fetch_failed",
            },
            "error",
        ),
    ],
)
def test_reminder_statuses_are_calculated_without_network(overrides, expected):
    settings = Settings(
        _env_file=None,
        official_reminder_overdue_grace_days=7,
    )
    row = source(**overrides)

    result = OfficialReminderService(settings).source_row(
        row,
        now=datetime(2026, 7, 25, tzinfo=UTC),
    )

    assert result["reminder_status"] == expected
    if row.last_checked_at:
        assert result["next_suggested_check_at"] == row.last_checked_at + timedelta(
            days=row.check_interval_days
        )
    else:
        assert result["next_suggested_check_at"] is None


def test_reminder_summary_and_patch_api_enforce_safe_interval(client, db_session):
    row = source()
    db_session.add(row)
    db_session.commit()

    summary = client.get("/official/reminders/summary")
    assert summary.status_code == 200
    assert summary.json()["total_sources"] == 1
    assert summary.json()["never_checked_count"] == 1
    assert summary.json()["batch_limit"] == 10

    for interval in (0, 366):
        invalid = client.patch(
            f"/official/sources/{row.id}/reminder",
            json={"reminder_enabled": True, "check_interval_days": interval},
        )
        assert invalid.status_code == 422

    updated = client.patch(
        f"/official/sources/{row.id}/reminder",
        json={"reminder_enabled": False, "check_interval_days": 45},
    )
    assert updated.status_code == 200
    assert updated.json()["reminder_enabled"] is False
    assert updated.json()["check_interval_days"] == 45


def test_page_loads_do_not_run_official_audit(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.official_audit_service.OfficialAuditService.audit",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    db_session.add(source())
    db_session.commit()

    assert client.get("/").status_code == 200
    assert client.get("/official").status_code == 200
    assert client.get("/official/reminders/summary").status_code == 200

    assert calls == []


def test_home_has_core_status_navigation_and_no_sensitive_debug_data(client, db_session):
    db_session.add(
        RagRetrievalProfile(
            profile_key="rag-safe",
            version="rag-safe-v1",
            configuration={},
            configuration_hash="a" * 64,
            is_active=True,
            is_baseline=False,
            evaluation_status="completed",
            promotion_eligible=True,
        )
    )
    db_session.commit()

    page = client.get("/")

    assert page.status_code == 200
    for text in (
        "当前 RAG Profile",
        "rag-safe-v1",
        "已索引笔记",
        "需要检查的官方来源",
        "待审批 Proposal",
        "索引异常",
        "学习工作台",
        "官方资料",
        "高级功能",
    ):
        assert text in page.text
    for forbidden in ("DEEPSEEK_API_KEY", "Token", "完整 Prompt", "C:\\"):
        assert forbidden not in page.text


def test_frontend_batches_existing_audit_endpoint_sequentially_and_caps_at_ten():
    source_text = Path("app/static/official_sources.js").read_text(encoding="utf-8")

    assert "const batch = candidates.slice(0, limit)" in source_text
    assert "for (let index = 0; index < batch.length; index += 1)" in source_text
    assert "await api(`/official/sources/${source.id}/audit`" in source_text
    assert "/official/sources/batch" not in source_text
    assert "/approve" not in source_text[source_text.index("async function auditDueSources"):source_text.index("async function loadNotes")]


def test_stage8_migration_is_minimal_and_follows_stage7_head():
    migration = Path(
        "alembic/versions/20260725_0010_add_official_source_reminders.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260725_0010"' in migration
    assert 'down_revision = "20260724_0009"' in migration
    assert '"reminder_enabled"' in migration
    assert '"check_interval_days"' in migration
    assert "create_table" not in migration
