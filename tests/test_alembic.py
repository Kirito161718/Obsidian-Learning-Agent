from pathlib import Path


def test_alembic_config_files_exist():
    assert Path("alembic.ini").exists()
    assert Path("alembic/env.py").exists()
    assert Path("alembic/script.py.mako").exists()


def test_initial_migration_exists():
    versions = list(Path("alembic/versions").glob("*.py"))
    assert versions
    assert any("init_tables" in path.name for path in versions)
