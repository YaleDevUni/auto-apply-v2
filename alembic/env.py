"""Alembic 환경 — SQLite 단일 DB (D3).

URL 은 `Config.set_main_option("sqlalchemy.url", ...)` 로 받은 값이 우선이고, 없으면
`Settings().database_url`(데이터 디렉터리의 db.sqlite3)이다. 앱은 aiosqlite 로 붙지만 마이그레이션은
이벤트 루프 없이도 돌도록(기동 전·테스트 fixture) 동기 드라이버로 바꿔 실행한다.
"""

from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL, make_url

from alembic import context
from auto_apply.adapters.repository.models import Base
from auto_apply.config import Settings

config = context.config

# 프로그램에서 부를 때(테스트·앱 기동)는 호출자의 로깅 설정을 덮어쓰지 않는다.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _sync_url() -> URL:
    url = make_url(config.get_main_option("sqlalchemy.url") or Settings().database_url)
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


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # SQLite 는 ALTER 가 제한적이라 batch 모드(테이블 재작성)로 렌더한다.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
