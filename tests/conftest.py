"""전역 테스트 설정."""

from pathlib import Path

import pytest
from alembic import command

from auto_apply.adapters.repository.migrate import alembic_config
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
