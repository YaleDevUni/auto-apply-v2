"""전역 테스트 설정."""

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_apply.adapters.repository.memory import InMemoryDatabase, InMemoryUnitOfWork
from auto_apply.adapters.repository.migrate import alembic_config
from auto_apply.adapters.repository.sqlite import SqliteUnitOfWork, build_engine
from auto_apply.config import Settings

# 게이트(make check)가 개발자 로컬 `.env` 에 좌우되지 않게 한다 — 안 그러면 테스트가 "코드"가
# 아니라 "이 머신의 설정"을 검사한다. 필요한 값은 각 테스트가 인자·monkeypatch 로 준다.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """기본 data_dir 은 사용자의 실제 platformdirs 경로다 — 테스트가 거기에 DB 를 만들지 않게."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    """Alembic head 까지 올린 빈 SQLite DB 의 (앱용 aiosqlite) URL."""
    path = (tmp_path / "db.sqlite3").as_posix()
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return f"sqlite+aiosqlite:///{path}"


@pytest.fixture(params=["memory", "sqlite"])
async def uow_factory(request: pytest.FixtureRequest):
    """UnitOfWork 두 구현(§A2) — repository contract test 가 둘 다에 같은 기대를 건다.

    sqlite 는 Alembic head 로 만든 실제 파일 DB 다 — 외부 인프라가 필요 없어 기본 실행된다.
    """
    if request.param == "memory":
        db = InMemoryDatabase()
        yield lambda: InMemoryUnitOfWork(db)
        return
    engine = build_engine(request.getfixturevalue("sqlite_url"))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield lambda: SqliteUnitOfWork(session_factory)
    await engine.dispose()
