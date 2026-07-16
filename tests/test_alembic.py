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
