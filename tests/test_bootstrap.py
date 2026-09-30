"""composition root 테스트 — 설정만 바꿔 구현이 교체되는지 확인 (§A2)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from pydantic import ValidationError

from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.adapters.facts.repository import RepositoryFactSource
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.profile.repository import RepositoryProfileSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.migrate import alembic_config
from auto_apply.adapters.repository.sqlite import SqliteUnitOfWork
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import build_container
from auto_apply.config import Settings
from auto_apply.contracts.dto import ApplicationRecord, PersistState
from auto_apply.contracts.experience import Experience, ExperienceFact
from auto_apply.contracts.jobs import JobKind, JobRecord
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState, ExperienceKind


def test_offline_profile_builds():
    c = build_container(Settings(storage="memory", llm_provider="stub"))
    assert isinstance(c.store, InMemoryBlobStore)


def test_storage_swap_by_config(tmp_path):
    c = build_container(Settings(storage="local", data_dir=tmp_path))
    assert isinstance(c.store, LocalBlobStore)


def test_resume_llm_keeps_slash_commands_locked():
    """외부 공고 텍스트가 프롬프트에 들어가는 인스턴스는 슬래시커맨드 표면을 열지 않는다

    (adapters/llm/claude_code_cli.py "turn()" 절)."""
    c = build_container(Settings(storage="memory", llm_provider="claude_cli"), port=8765)
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


_RECORD = ApplicationRecord(
    application_id="app_1", url="https://jobs.example.com/1", domain="jobs.example.com"
)


def _draft() -> PersistState:
    return PersistState(
        application_id="app_1",
        run_id=None,
        state=ApplicationState.DRAFT,
        at=datetime(2026, 10, 1, tzinfo=UTC),
    )


async def test_memory_repository_shares_rows_across_uows():
    c = build_container(Settings(storage="memory", repository="memory"))
    state = _draft()
    async with c.uow() as uow:
        assert isinstance(uow, InMemoryUnitOfWork)
        await uow.applications.add(_RECORD, state)
    async with c.uow() as uow:
        assert await uow.applications.history("app_1") == [state]


async def test_sqlite_repository_roundtrip_through_container(tmp_path):
    cfg = Settings(storage="memory", repository="sqlite", data_dir=tmp_path)
    command.upgrade(alembic_config(cfg.database_url.replace("+aiosqlite", "")), "head")
    c = build_container(cfg)
    state = _draft()
    async with c.uow() as uow:
        assert isinstance(uow, SqliteUnitOfWork)
        await uow.applications.add(_RECORD, state)
        await uow.commit()
    async with c.uow() as uow:
        assert await uow.applications.history("app_1") == [state]


def test_container_exposes_document_service():
    c = build_container(Settings(storage="memory", repository="memory"))
    assert c.documents is not None


def test_blob_store_root_is_files_dir_in_data_dir(tmp_path):
    """업로드·생성 파일 바이트는 데이터 디렉터리의 `files/` 에 (§A1)."""
    cfg = Settings(storage="local", repository="memory", data_dir=tmp_path)
    c = build_container(cfg)
    assert isinstance(c.store, LocalBlobStore)
    assert cfg.files_dir == tmp_path / "files"
    assert c.store._root == tmp_path / "files"


def test_all_paths_derive_from_data_dir(tmp_path, monkeypatch):
    """cwd 기준 `./config` 경로가 없다 — 어느 폴더에서 띄워도 같은 데이터를 읽는다 (T1.1)."""
    monkeypatch.chdir(tmp_path)
    cfg = Settings(_env_file=None, data_dir=tmp_path / "data")
    assert cfg.guide_dir == tmp_path / "data" / "guides"
    paths = [v for v in cfg.model_dump().values() if isinstance(v, Path)]
    assert paths == [cfg.data_dir]
    assert cfg.files_dir.is_relative_to(cfg.data_dir)
    assert cfg.chrome_profile_dir == tmp_path / "data" / "chrome-profile"


def test_browser_host_is_lazy_chrome_on_app_profile(tmp_path):
    """조립만으로 브라우저를 띄우거나 프로필을 만들지 않는다 — 첫 사용 때 뜬다 (§A1, D6)."""
    c = build_container(Settings(storage="memory", repository="memory", data_dir=tmp_path))
    assert isinstance(c.browser, PlaywrightBrowserHost)
    assert c.browser.running is False
    assert c.browser._profile_dir == tmp_path / "chrome-profile"
    assert c.browser._bundled is False
    assert c.browser._headless is False
    assert not (tmp_path / "chrome-profile").exists()


async def test_resume_pipeline_reads_profile_and_facts_from_repository():
    c = build_container(Settings(storage="memory", repository="memory"))
    assert isinstance(c.facts, RepositoryFactSource)
    assert isinstance(c.profile, RepositoryProfileSource)
    async with c.uow() as uow:
        await uow.profiles.save(Profile(user_id="u1", name="홍길동"))
        await uow.experiences.save(
            Experience(
                id="p1",
                user_id="u1",
                kind=ExperienceKind.PROJECT,
                name="P",
                facts=[ExperienceFact(id="f1", text="개발")],
            )
        )
        await uow.commit()
    assert (await c.profile.get("u1")).name == "홍길동"
    assert [f.id for f in await c.facts.list_for_user("u1")] == ["f1"]


async def test_fill_handler_is_registered_and_fails_closed_without_runtime():
    """stub(오프라인) 조합은 빈 스크립트 런타임 — fill job 은 브라우저를 띄우지 않고 FAILED."""
    c = build_container(Settings(storage="memory", repository="memory", llm_provider="stub"))
    fill = c.runner._handlers[JobKind.FILL]
    app = await c.applications.create("https://jobs.example.com/p/1")
    await c.applications.transition(app.application_id, ApplicationState.QUEUED, run_id=None)
    now = datetime.now(UTC)
    job = JobRecord(
        job_id="job_1", kind=JobKind.FILL, application_id=app.application_id,
        created_at=now, run_after=now,
    )  # fmt: skip
    await fill(job)
    assert await c.applications.current_state(app.application_id) is ApplicationState.FAILED
    assert c.browser.running is False


def test_human_wait_is_a_setting_and_reentry_is_wired():
    """T2.6 이관 — 사람 대기 상한은 설정(HUMAN_WAIT_S), 늦은 답 → 재진입 run 통로가 조립된다."""
    assert Settings().human_wait_s == 600
    with pytest.raises(ValidationError):
        Settings(human_wait_s=0)
    c = build_container(Settings(storage="memory", repository="memory", human_wait_s=30))
    assert c.reentry is not None
