"""Alembic 리비전 = models.py (§A2 repository). 둘이 갈리면 마이그레이션을 빠뜨린 것이다."""

import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from auto_apply.adapters.repository.migrate import MigrationError, alembic_config, upgrade_to_head
from auto_apply.adapters.repository.models import Base

_PROFILE_TABLES = {"profiles", "experiences", "answers", "documents"}  # 0002 (T1.1)


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
    assert {"applications", "application_state_history", "runs"} | _PROFILE_TABLES <= tables


def test_0002_adds_profile_tables_and_downgrade_removes_only_them(tmp_path):
    """0002 up/down (T1.1): 0001 의 지원 건 데이터는 0002 를 오르내려도 남는다."""
    db = tmp_path / "db.sqlite3"
    cfg = alembic_config(f"sqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0001")
    assert _PROFILE_TABLES.isdisjoint(_tables(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "insert into applications (id, state, payload, last_event_id)"
            " values ('app_1', 'evaluating', '{}', 0)"
        )

    command.upgrade(cfg, "0002")
    assert _tables(db) >= _PROFILE_TABLES
    with sqlite3.connect(db) as conn:
        conn.execute(
            "insert into answers (id, user_id, question_key, answer, updated_at)"
            " values ('a1', 'u1', 'k', 'v', '2026-09-30 00:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):  # (user_id, question_key) 유일
            conn.execute(
                "insert into answers (id, user_id, question_key, answer, updated_at)"
                " values ('a2', 'u1', 'k', 'v', '2026-09-30 00:00:00')"
            )

    command.downgrade(cfg, "0001")
    assert _PROFILE_TABLES.isdisjoint(_tables(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("select id from applications").fetchall() == [("app_1",)]


def _columns(db, table: str) -> dict[str, bool]:
    """컬럼 이름 → NOT NULL 여부."""
    with sqlite3.connect(db) as conn:
        return {r[1]: bool(r[3]) for r in conn.execute(f"pragma table_info({table})")}


def test_0003_upgrades_existing_m1_data_and_downgrade_restores_0002(tmp_path):
    """0003 up/down (T3.1): M1 이후 DB(프로필 데이터 있음, 지원 건 0행)가 그대로 오르내린다."""
    db = tmp_path / "db.sqlite3"
    cfg = alembic_config(f"sqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0002")
    with sqlite3.connect(db) as conn:
        conn.execute("insert into profiles (user_id, payload) values ('u1', '{}')")
        conn.execute(
            "insert into answers (id, user_id, question_key, answer, updated_at)"
            " values ('a1', 'u1', 'k', 'v', '2026-09-30 00:00:00')"
        )

    command.upgrade(cfg, "0003")
    apps = _columns(db, "applications")
    assert {"url", "domain", "submit_mode"} <= apps.keys()
    assert _columns(db, "application_state_history")["run_id"] is False  # 사람 조작 전이는 run 없음
    assert {"result", "input_tokens", "output_tokens", "transcript_path"} <= _columns(
        db, "runs"
    ).keys()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "insert into applications (id, state, payload, last_event_id)"
            " values ('app_1', 'draft', '{}', 1)"
        )
        conn.execute(
            "insert into application_state_history (application_id, run_id, state, payload)"
            " values ('app_1', NULL, 'draft', '{}')"
        )
        # 새 컬럼을 빼먹은 행도 안전한 쪽 기본값(dry_run, 절대 규칙 2)으로 들어간다.
        assert conn.execute("select submit_mode from applications").fetchone() == ("dry_run",)
        assert conn.execute("select count(*) from answers").fetchone() == (1,)

    command.downgrade(cfg, "0002")
    assert {"url", "domain", "submit_mode"}.isdisjoint(_columns(db, "applications"))
    assert _columns(db, "application_state_history")["run_id"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute("select run_id from application_state_history").fetchall() == [("",)]
        assert conn.execute("select user_id from profiles").fetchall() == [("u1",)]


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
