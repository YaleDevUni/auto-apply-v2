"""Alembic 리비전 = models.py (§A2 repository). 둘이 갈리면 마이그레이션을 빠뜨린 것이다."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from auto_apply.adapters.repository.models import Base
from tests._db import alembic_config


def _sync(url: str) -> str:
    return url.replace("sqlite+aiosqlite://", "sqlite://")


def test_head_schema_matches_models(sqlite_url):
    engine = create_engine(_sync(sqlite_url))
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_initial_schema_has_minimal_tables(sqlite_url):
    engine = create_engine(_sync(sqlite_url))
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"applications", "application_state_history", "runs"} <= tables


def test_downgrade_to_base_and_back(sqlite_url):
    cfg = alembic_config(_sync(sqlite_url))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_upgrade_creates_missing_data_dir(tmp_path):
    """첫 기동 때 데이터 디렉터리가 아직 없어도 마이그레이션이 돈다."""
    db = tmp_path / "not-yet" / "db.sqlite3"
    command.upgrade(alembic_config(f"sqlite:///{db.as_posix()}"), "head")
    assert db.is_file()
