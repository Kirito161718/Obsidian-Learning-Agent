from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import Settings


def test_alembic_config_files_exist():
    assert Path("alembic.ini").exists()
    assert Path("alembic/env.py").exists()
    assert Path("alembic/script.py.mako").exists()


def test_initial_migration_exists():
    versions = list(Path("alembic/versions").glob("*.py"))
    assert versions
    assert any("init_tables" in path.name for path in versions)


def test_alembic_reuses_settings_database_url_object():
    env_source = Path("alembic/env.py").read_text(encoding="utf-8")

    assert "database_url = settings.database_url" in env_source
    assert "create_engine(database_url" in env_source
    assert "str(settings.database_url)" not in env_source
    assert "config.set_main_option" not in env_source


def test_database_url_round_trips_special_password_characters():
    password = "p@ss:/#%word"
    settings = Settings(_env_file=None, mysql_password=password)

    rendered = settings.database_url.render_as_string(hide_password=False)

    assert make_url(rendered).password == password


def test_stage3_migration_has_expected_revision_and_objects():
    source = Path("alembic/versions/20260718_0004_add_learning_sessions.py").read_text(encoding="utf-8")
    assert 'revision = "20260718_0004"' in source
    assert 'down_revision = "20260717_0003"' in source
    assert '"learning_sessions"' in source
    assert '"learning_session_reviews"' in source
    assert 'op.add_column("qa_history"' in source
    assert 'ondelete="SET NULL"' in source
    assert 'uq_learning_session_reviews_session_id' in source


def test_stage6_migration_has_single_expected_revision_and_persistent_checkpoint_tables():
    source = Path("alembic/versions/20260721_0007_add_official_source_audit.py").read_text(encoding="utf-8")
    assert 'revision = "20260721_0007"' in source
    assert 'down_revision = "20260720_0006"' in source
    for table in (
        "official_sources",
        "official_source_snapshots",
        "official_source_note_links",
        "update_proposals",
        "update_proposal_items",
        "approval_requests",
        "note_versions",
        "official_checkpoints",
        "official_checkpoint_writes",
    ):
        assert f'"{table}"' in source
    assert "uq_official_snapshots_source_hash" in source
    assert "fk_update_proposals_approval_request" in source
    assert 'mysql.DATETIME(fsp=6)' in source
    assert 'sa.Column("created_at", precise_datetime()' in source
    assert 'mysql.MEDIUMBLOB()' in source


def test_stage7a_migration_adds_only_nullable_observability_correlation_fields():
    source = Path(
        "alembic/versions/20260722_0008_add_langsmith_observability.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "20260722_0008"' in source
    assert 'down_revision = "20260721_0007"' in source
    for column in (
        "observability_provider",
        "external_trace_id",
        "observability_status",
        "observability_error_code",
    ):
        assert f'Column("{column}"' in source
    assert "nullable=True" in source
