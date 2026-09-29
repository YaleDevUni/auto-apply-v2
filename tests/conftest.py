"""전역 테스트 설정."""

from pathlib import Path

import pytest
from alembic import command

from auto_apply.config import Settings
from tests._db import alembic_config

# 게이트(make check)가 개발자 로컬 `.env` 에 좌우되지 않게 한다 — 안 그러면 테스트가 "코드"가
# 아니라 "이 머신의 설정"을 검사한다. 필요한 값은 각 테스트가 인자·monkeypatch 로 준다.
Settings.model_config["env_file"] = None


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    """Alembic head 까지 올린 빈 SQLite DB 의 (앱용 aiosqlite) URL."""
    path = (tmp_path / "db.sqlite3").as_posix()
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return f"sqlite+aiosqlite:///{path}"
