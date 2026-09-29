"""테스트용 Alembic 헬퍼 — 스키마는 create_all 이 아니라 실제 리비전으로 만든다."""

from pathlib import Path

from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg
