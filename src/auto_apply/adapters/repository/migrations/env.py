"""Alembic 환경 — SQLite 단일 DB (D3).

URL 은 `Config.set_main_option("sqlalchemy.url", ...)` 로 받은 값이 우선이고, 없으면
`load_settings().database_url`(데이터 디렉터리의 db.sqlite3)이다. 앱은 aiosqlite 로 붙지만
마이그레이션은 이벤트 루프 없이도 돌도록(기동 전·테스트 fixture) 동기 드라이버로 바꿔 실행한다.
"""

from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import Connection, create_engine, event, pool
from sqlalchemy.engine import URL, make_url

from auto_apply.adapters.repository.models import Base
from auto_apply.config import load_settings

config = context.config

# 프로그램에서 부를 때(테스트·앱 기동)는 호출자의 로깅 설정을 덮어쓰지 않는다.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _sync_url() -> URL:
    url = make_url(config.get_main_option("sqlalchemy.url") or load_settings().database_url)
    if url.drivername == "sqlite+aiosqlite":
        url = url.set(drivername="sqlite")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _atomic_sqlite_engine(url: URL) -> Any:
    """pysqlite 는 기본으로 DDL 앞에서 트랜잭션을 커밋해 버린다 — 중간 실패 시 테이블 일부만 남고
    alembic_version 은 비어, 다음 기동도 같은 자리에서 영원히 막힌다. SQLAlchemy 공식
    pysqlite 레시피대로 드라이버의 트랜잭션 관리를 끄고 BEGIN 을 직접 보내 한 트랜잭션으로 묶는다.
    """
    engine = create_engine(url, poolclass=pool.NullPool)

    @event.listens_for(engine, "connect")
    def _no_driver_tx(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN")

    return engine


def run_migrations_online() -> None:
    engine = _atomic_sqlite_engine(_sync_url())
    with engine.connect() as connection:
        # SQLite 는 ALTER 가 제한적이라 batch 모드(테이블 재작성)로 렌더한다.
        # transactional_ddl=True: 리비전별이 아니라 upgrade 전체를 한 트랜잭션으로.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
