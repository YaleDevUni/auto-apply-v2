"""AsideCliExecutor — Aside CLI subprocess 를 쓰는 WebAgentExecutor 구현 (§2.4b).

subprocess 는 뜨지 않는다 — `asyncio.create_subprocess_exec` 를 모킹해서 어댑터가 만드는
프롬프트/세션 id 추출/자격증명 파일 생명주기/에러 매핑만 검증한다(실제 CLI 대상 실측은
memory ats-web-agent-executor-design 에 기록됨). 가장 중요한 검증은 "비밀번호 문자열이
subprocess 인자 어디에도 안 들어가는가"다.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.credentials.static import StaticCredentialSource
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.web_agent import aside_cli as module
from auto_apply.adapters.web_agent.aside_cli import AsideCliExecutor
from auto_apply.contracts.resume_content import AssembledResume
from auto_apply.contracts.web_agent import WebAgentSessionRef, WebAgentTask
from auto_apply.domain.enums import AttemptOutcome
from auto_apply.domain.errors import (
    CaptchaEncountered,
    WebAgentExecutionError,
    WebAgentLoginFailed,
    WebAgentTaskFailed,
)
from auto_apply.ports.credentials import Credential

# ATS/자체구축 실행 기반은 최후순위로 미룬다(사용자 지시) — 다른 테스트의 병목이라 스킵해둔다.
# 재착수할 땐 이 마크만 지우면 된다.
pytestmark = pytest.mark.skip(reason="ATS/자체구축 실행 기반은 최후순위 — 사용자 지시로 보류")

_SESSION_DIR_NAME = "2026-08-20_testsess"
_SECRET_PASSWORD = "s3cr3t-p@ss"


def _resume() -> AssembledResume:
    return AssembledResume(name="테스트", summary="요약")


def _task(*, credential_key: str | None = None) -> WebAgentTask:
    return WebAgentTask(
        application_id="app_1",
        apply_url="https://ats.example.com/jobs/1/apply",
        resume=_resume(),
        credential_key=credential_key,
    )


def _executor(sessions_root: Path, *, credentials=None, store=None) -> AsideCliExecutor:
    return AsideCliExecutor(
        credentials or StaticCredentialSource(),
        store or InMemoryBlobStore(),
        SystemClock(),
        sessions_root=sessions_root,
    )


def _patch_exec(fake_exec):
    return patch.object(module.asyncio, "create_subprocess_exec", fake_exec)


def _make_fake_exec(
    sessions_root: Path,
    *,
    captured: list[list[str]],
    stdout_lines: list[bytes] | None = None,
    write_screenshot: bool = True,
):
    """1번째 호출에서 세션 디렉터리를 새로 만든다(실제 aside 가 세션을 여는 것과 동일 효과).

    2번째 호출(fill 프롬프트) 시점엔 attachments/ 가 이미 만들어져 있다는 전제(어댑터가
    직접 만든다) 하에 그 안에 스크린샷 파일을 써준다 — 실제로는 aside 가 쓰지만 여기선
    subprocess 를 안 띄우니 테스트가 대신 흉내낸다.
    """
    call_count = 0

    def _side_effects(n: int) -> None:
        if n == 0:
            (sessions_root / _SESSION_DIR_NAME).mkdir(parents=True)
        elif write_screenshot:
            shot = sessions_root / _SESSION_DIR_NAME / "attachments" / "fill_screenshot.png"
            if shot.parent.is_dir():
                shot.write_bytes(b"PNGDATA")

    async def fake_exec(*args: str, **_kwargs: object) -> AsyncMock:
        nonlocal call_count
        captured.append(list(args))
        _side_effects(call_count)
        stdout = b"ok"
        if stdout_lines and call_count < len(stdout_lines):
            stdout = stdout_lines[call_count]
        call_count += 1
        proc = AsyncMock()
        proc.communicate.return_value = (stdout, b"")
        proc.returncode = 0
        return proc

    return fake_exec


async def test_fill_never_leaks_password_into_subprocess_args(tmp_path: Path):
    creds = StaticCredentialSource({"acme": Credential(username="u1", password=_SECRET_PASSWORD)})
    ex = _executor(tmp_path, credentials=creds)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        await ex.fill(_task(credential_key="acme"))

    flat = " ".join(a for call in captured for a in call)
    assert _SECRET_PASSWORD not in flat


async def test_fill_writes_credential_file_then_deletes_it(tmp_path: Path):
    creds = StaticCredentialSource({"acme": Credential(username="u1", password=_SECRET_PASSWORD)})
    ex = _executor(tmp_path, credentials=creds)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        await ex.fill(_task(credential_key="acme"))

    cred_path = tmp_path / _SESSION_DIR_NAME / "attachments" / "login.json"
    assert not cred_path.exists()
    # 로그인 지시가 프롬프트에 파일 경로로는 들어간다 — 값이 아니라 경로 참조라는 걸 확인.
    assert any(str(cred_path) in a for call in captured for a in call)


async def test_fill_extracts_session_id_via_directory_diff(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        result = await ex.fill(_task())
    assert result.session == WebAgentSessionRef(session_id="testsess")


async def test_fill_second_call_uses_session_flag(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        await ex.fill(_task())
    assert len(captured) == 2
    assert "--session" not in captured[0]
    assert "--session" in captured[1]
    assert captured[1][captured[1].index("--session") + 1] == "testsess"


async def test_fill_without_credential_key_skips_login_step(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        await ex.fill(_task())
    assert "로그인" not in captured[1][-1]


async def test_fill_uploads_screenshot_to_blobstore(tmp_path: Path):
    store = InMemoryBlobStore()
    ex = _executor(tmp_path, store=store)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        result = await ex.fill(_task())
    assert await store.get(result.screenshot_key) == b"PNGDATA"


async def test_fill_raises_task_failed_when_screenshot_missing(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    with (
        _patch_exec(_make_fake_exec(tmp_path, captured=captured, write_screenshot=False)),
        pytest.raises(WebAgentTaskFailed),
    ):
        await ex.fill(_task())


async def test_fill_raises_captcha_encountered_on_signature(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    fake = _make_fake_exec(tmp_path, captured=captured, stdout_lines=[b"ok", b"CAPTCHA detected"])
    with _patch_exec(fake), pytest.raises(CaptchaEncountered):
        await ex.fill(_task())


async def test_fill_raises_login_failed_on_signature(tmp_path: Path):
    creds = StaticCredentialSource({"acme": Credential(username="u1", password="p")})
    ex = _executor(tmp_path, credentials=creds)
    captured: list[list[str]] = []
    fake = _make_fake_exec(
        tmp_path, captured=captured, stdout_lines=[b"ok", b"Login failed: invalid credentials"]
    )
    with _patch_exec(fake), pytest.raises(WebAgentLoginFailed):
        await ex.fill(_task(credential_key="acme"))


async def test_fill_raises_login_failed_when_key_missing_from_queue(tmp_path: Path):
    ex = _executor(tmp_path, credentials=StaticCredentialSource({}))
    captured: list[list[str]] = []
    with (
        _patch_exec(_make_fake_exec(tmp_path, captured=captured)),
        pytest.raises(WebAgentLoginFailed),
    ):
        await ex.fill(_task(credential_key="missing-company"))


async def test_submit_uses_session_flag_and_returns_succeeded(tmp_path: Path):
    ex = _executor(tmp_path)
    captured: list[list[str]] = []
    with _patch_exec(_make_fake_exec(tmp_path, captured=captured)):
        result = await ex.submit(WebAgentSessionRef(session_id="testsess"))
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert "--session" in captured[0]
    assert captured[0][captured[0].index("--session") + 1] == "testsess"


async def test_run_raises_execution_error_on_nonzero_exit(tmp_path: Path):
    ex = _executor(tmp_path)

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"boom")
        proc.returncode = 1
        return proc

    with _patch_exec(fake_exec), pytest.raises(WebAgentExecutionError, match="boom"):
        await ex.submit(WebAgentSessionRef(session_id="testsess"))


async def test_run_raises_execution_error_on_timeout(tmp_path: Path):
    ex = _executor(tmp_path)

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        proc = AsyncMock()
        proc.kill = lambda: None
        return proc

    async def fake_wait_for(coro, **_kwargs: object):
        coro.close()  # 미실행 코루틴을 닫아 RuntimeWarning 방지
        raise TimeoutError

    with (
        patch.object(module.asyncio, "wait_for", fake_wait_for),
        _patch_exec(fake_exec),
        pytest.raises(WebAgentExecutionError, match="타임아웃"),
    ):
        await ex.submit(WebAgentSessionRef(session_id="testsess"))
