"""composition root 테스트 — 설정만 바꿔 구현이 교체되는지 확인 (§A2)."""

from alembic import command

from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.sqlite import SqliteUnitOfWork
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import build_container
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.domain.enums import ApplicationState
from tests._db import alembic_config


def test_offline_profile_builds():
    c = build_container(Settings(storage="memory", llm_provider="stub"))
    assert isinstance(c.store, InMemoryBlobStore)


def test_storage_swap_by_config(tmp_path):
    c = build_container(Settings(storage="local", data_dir=tmp_path))
    assert isinstance(c.store, LocalBlobStore)


def test_resume_llm_keeps_slash_commands_locked():
    """외부 공고 텍스트가 프롬프트에 들어가는 인스턴스는 슬래시커맨드 표면을 열지 않는다

    (adapters/llm/claude_code_cli.py "turn()" 절)."""
    c = build_container(Settings(storage="memory", llm_provider="claude_cli"))
    assert isinstance(c.llm, ClaudeCodeCliLLM)
    assert c.llm._allow_slash_commands is False


def test_dry_run_only_defaults_true():
    """안전장치는 기본값이 안전한 쪽이어야 한다 (00-product 절대 규칙 2).

    `_env_file=None` 으로 개발자의 `.env` 를 일부러 안 읽는다 — 안 그러면 이 테스트는
    "코드의 기본값"이 아니라 "지금 이 머신의 설정"을 검사하게 된다.
    """
    assert Settings(_env_file=None).dry_run_only is True


def test_repository_defaults_to_sqlite_in_data_dir(tmp_path):
    cfg = Settings(_env_file=None, data_dir=tmp_path)
    assert cfg.repository == "sqlite"
    assert cfg.database_url.startswith("sqlite+aiosqlite:///")
    assert cfg.database_url.endswith(f"{tmp_path.resolve().as_posix()}/db.sqlite3")


async def test_memory_repository_shares_rows_across_uows():
    c = build_container(Settings(storage="memory", repository="memory"))
    state = PersistState(
        application_id="app_1", workflow_run_id="run_1", state=ApplicationState.EVALUATING
    )
    async with c.uow() as uow:
        assert isinstance(uow, InMemoryUnitOfWork)
        await uow.applications.upsert_state(state)
    async with c.uow() as uow:
        assert await uow.applications.history("app_1") == [state]


async def test_sqlite_repository_roundtrip_through_container(tmp_path):
    cfg = Settings(storage="memory", repository="sqlite", data_dir=tmp_path)
    command.upgrade(alembic_config(cfg.database_url.replace("+aiosqlite", "")), "head")
    c = build_container(cfg)
    state = PersistState(
        application_id="app_1", workflow_run_id="run_1", state=ApplicationState.EVALUATING
    )
    async with c.uow() as uow:
        assert isinstance(uow, SqliteUnitOfWork)
        await uow.applications.upsert_state(state)
        await uow.commit()
    async with c.uow() as uow:
        assert await uow.applications.history("app_1") == [state]


def test_container_exposes_document_service():
    c = build_container(Settings(storage="memory", repository="memory"))
    assert c.documents is not None
