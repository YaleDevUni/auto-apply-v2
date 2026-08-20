"""WebAgentExecutor contract test (ARCHITECTURE.md §11.2, §2.4b).

ports/web_agent.py 의 docstring 이 계약이다:
  - fill() 은 제출까지 가지 않는다 — 채우고 스크린샷 찍은 상태의 session 을 돌려준다.
  - submit() 은 fill() 이 돌려준 session 으로 이어받으면 성공한다.

ReplayWebAgentExecutor 는 진짜 subprocess 없이 이 계약을 재현하는 대역이므로, 같은 시나리오를
AsideCliExecutor(subprocess 모킹)에도 그대로 돌려 두 구현이 같은 계약을 지키는지 확인한다.
개별 실패 분류(로그인 실패/CAPTCHA 등)는 각 어댑터 전용 테스트에서 이미 다룬다 — 여기는
"교체 가능성"(§11.5)만 증명한다.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.credentials.static import StaticCredentialSource
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.web_agent import aside_cli as aside_cli_module
from auto_apply.adapters.web_agent.aside_cli import AsideCliExecutor
from auto_apply.adapters.web_agent.replay import ReplayWebAgentExecutor
from auto_apply.contracts.resume_content import AssembledResume
from auto_apply.contracts.web_agent import WebAgentTask
from auto_apply.domain.enums import AttemptOutcome
from auto_apply.ports.web_agent import WebAgentExecutor


def _task() -> WebAgentTask:
    return WebAgentTask(
        application_id="app_1",
        apply_url="https://ats.example.com/jobs/1/apply",
        resume=AssembledResume(name="테스트", summary="요약"),
    )


def _side_effects(session_dir: Path, args: tuple[str, ...]) -> None:
    if not session_dir.is_dir():
        session_dir.mkdir(parents=True)
    elif "--session" in args:
        shot = session_dir / "attachments" / "fill_screenshot.png"
        if shot.parent.is_dir():
            shot.write_bytes(b"PNGDATA")


def _fake_exec(session_dir: Path):
    async def fake_exec(*args: str, **_kwargs: object) -> AsyncMock:
        _side_effects(session_dir, args)
        proc = AsyncMock()
        proc.communicate.return_value = (b"ok", b"")
        proc.returncode = 0
        return proc

    return fake_exec


@pytest.fixture(params=["replay", "aside_cli"])
def executor(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[WebAgentExecutor]:
    if request.param == "replay":
        yield ReplayWebAgentExecutor(SystemClock())
        return
    ex = AsideCliExecutor(
        StaticCredentialSource(), InMemoryBlobStore(), SystemClock(), sessions_root=tmp_path
    )
    session_dir = tmp_path / "2026-08-20_contractsess"
    with patch.object(aside_cli_module.asyncio, "create_subprocess_exec", _fake_exec(session_dir)):
        yield ex


async def test_fill_returns_screenshot_without_submitting(executor: WebAgentExecutor) -> None:
    result = await executor.fill(_task())
    assert result.screenshot_key
    assert result.session.session_id
    assert result.summary


async def test_submit_after_fill_succeeds(executor: WebAgentExecutor) -> None:
    filled = await executor.fill(_task())
    result = await executor.submit(filled.session)
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is not None
