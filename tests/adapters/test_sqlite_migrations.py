"""Alembic 리비전 = models.py (§A2 repository). 둘이 갈리면 마이그레이션을 빠뜨린 것이다."""

import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from auto_apply.adapters.repository.migrate import MigrationError, alembic_config, upgrade_to_head
from auto_apply.adapters.repository.models import Base


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


def _tables(db) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def test_failed_upgrade_leaves_no_partial_schema(tmp_path):
    """pysqlite 는 기본으로 DDL 을 트랜잭션 밖에서 돈다 — 중간 실패 시 테이블 일부만 남고
    alembic_version 이 비어 다음 기동도 영원히 같은 자리에서 막힌다.
    upgrade 는 원자적이어야 한다."""
    db = tmp_path / "db.sqlite3"
    with sqlite3.connect(db) as conn:
        # 0001 의 마지막 테이블을 미리 심어 upgrade 를 중간에서 실패시킨다.
        conn.execute("create table runs (id integer primary key)")

    with pytest.raises(MigrationError) as exc:
        upgrade_to_head(f"sqlite+aiosqlite:///{db.as_posix()}")

    assert str(db) in str(exc.value)
    assert "runs" in str(exc.value)
    assert "\n" not in str(exc.value)
    assert _tables(db) == {"runs"}
