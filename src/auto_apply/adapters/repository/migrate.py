"""Alembic 을 코드에서 돌린다 (§A1: 기동 때 자동 적용).

설치본(`uv tool install`)에는 alembic.ini 도 저장소 체크아웃도 없다 — 리비전 스크립트는 패키지 안
(`migrations/`)에 두고 Config 를 여기서 직접 조립한다.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _escape(value: str) -> str:
    # Config 옵션은 configparser 보간을 거친다 — 경로·URL 에 섞인 `%` 를 보간식으로 읽지 않게.
    return value.replace("%", "%%")


def alembic_config(database_url: str) -> Config:
    """앱 URL(aiosqlite)이든 동기 URL 이든 받는다 — env.py 가 동기 드라이버로 바꿔 돈다."""
    cfg = Config()
    cfg.set_main_option("script_location", _escape(str(MIGRATIONS_DIR)))
    cfg.set_main_option("sqlalchemy.url", _escape(database_url))
    # 호출자(앱·테스트)의 로깅 설정을 덮어쓰지 않는다.
    cfg.attributes["configure_logger"] = False
    return cfg


class MigrationError(RuntimeError):
    """사람이 읽을 한 줄: 어느 DB 파일에서 왜 실패했는지.

    스키마는 실패 전 상태 그대로다(env.py 가 upgrade 전체를 한 트랜잭션으로 돈다).
    """


def _one_line_cause(e: Exception) -> str:
    cause = e.orig if isinstance(e, DBAPIError) and e.orig is not None else e
    return " ".join(str(cause).split()) or type(cause).__name__


def upgrade_to_head(database_url: str) -> None:
    try:
        command.upgrade(alembic_config(database_url), "head")
    except (SQLAlchemyError, CommandError, OSError) as e:
        db = make_url(database_url).database or database_url
        raise MigrationError(f"DB 마이그레이션 실패 — {db}: {_one_line_cause(e)}") from e
