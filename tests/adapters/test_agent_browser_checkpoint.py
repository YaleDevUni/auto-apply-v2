"""AgentBrowserExecutor 의 SUPERVISED 체크포인트 배선 (§2.4c, PlaywrightExecutor 와 동일 계약).

test_playwright_checkpoint.py 와 같은 시나리오를 agent-browser 엔진에 대고 돌린다 — 두
executor 가 CheckpointWaiter 를 같은 방식으로 쓰는지 확인한다. 공유 계약(dry_run/captcha/
auth 등)은 tests/ports/test_executor_contract.py 가 이미 돈다.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from auto_apply.adapters.checkpoint.memory import InMemoryCheckpointStore
from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.executor._checkpoint import CheckpointWaiter
from auto_apply.adapters.executor.agent_browser import AgentBrowserExecutor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, ExecutionContext, NotifyEvent
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import CheckpointDeclined

pytestmark = pytest.mark.integration

_FORM_HTML = (
    '<form id="form"><input id="email"/><button id="submit" type="button">보내기</button></form>'
)


class _FixedNonceNotifier:
    """항상 같은 nonce 를 발급한다 — 테스트가 store 에 그 nonce 로 미리 결정을 심을 수 있게."""

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        self.requests.append(req)
        return DecisionTicket(ticket_id="tkt_fixed", nonce="nonce_fixed")

    async def notify(self, event: NotifyEvent) -> None:
        pass


def _write_html(path: Path, body: str) -> str:
    path.write_text(f"<!doctype html><html><body>{body}</body></html>")
    return path.as_uri()


def _auth_dir(tmp_path: Path) -> Path:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "fixture.json").write_text('{"cookies": [], "origins": []}')
    return auth_dir


def _executor(tmp_path: Path, notifier, store) -> AgentBrowserExecutor:
    checkpoint = CheckpointWaiter(
        notifier,
        store,
        InMemoryBlobStore(),
        UuidIdGen(),
        timeout=timedelta(milliseconds=300),
        poll_interval=timedelta(milliseconds=20),
    )
    return AgentBrowserExecutor(
        SystemClock(), InMemoryBlobStore(), auth_dir=_auth_dir(tmp_path), checkpoint=checkpoint
    )


def _recipe(goto: str) -> AutomationRecipe:
    return AutomationRecipe(
        platform="fixture",
        version=1,
        status="candidate",
        form_hash="h-ab-checkpoint-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal=goto),
            Action(type=ActionType.FILL, selector="#email", value_ref="profile.email"),
            Action(type=ActionType.SUBMIT, selector="#submit", timeout_ms=300),
        ],
        success_signals=["완료"],
    )


async def test_submit_is_always_checkpointed_even_without_the_flag(tmp_path: Path) -> None:
    """SUBMIT 은 recipe 가 checkpoint=True 를 안 세워도 SUPERVISED 에서 항상 막힌다."""
    form_url = _write_html(tmp_path / "form.html", _FORM_HTML)
    notifier = _FixedNonceNotifier()
    store = InMemoryCheckpointStore()
    executor = _executor(tmp_path, notifier, store)
    await store.record_decision("nonce_fixed", approved=True)
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile={"email": "a@b.com"})

    result = await executor.run(_recipe(form_url), ctx, ExecutionMode.SUPERVISED)

    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is not None
    assert len(notifier.requests) == 1
    assert notifier.requests[0].checkpoint is True


async def test_declined_checkpoint_raises_checkpoint_declined(tmp_path: Path) -> None:
    form_url = _write_html(tmp_path / "form.html", _FORM_HTML)
    notifier = _FixedNonceNotifier()
    store = InMemoryCheckpointStore()
    executor = _executor(tmp_path, notifier, store)
    await store.record_decision("nonce_fixed", approved=False)
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile={"email": "a@b.com"})

    with pytest.raises(CheckpointDeclined):
        await executor.run(_recipe(form_url), ctx, ExecutionMode.SUPERVISED)


async def test_timeout_raises_checkpoint_declined(tmp_path: Path) -> None:
    form_url = _write_html(tmp_path / "form.html", _FORM_HTML)
    notifier = _FixedNonceNotifier()
    store = InMemoryCheckpointStore()  # 결정을 아무것도 안 심는다
    executor = _executor(tmp_path, notifier, store)
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile={"email": "a@b.com"})

    with pytest.raises(CheckpointDeclined):
        await executor.run(_recipe(form_url), ctx, ExecutionMode.SUPERVISED)


async def test_dry_run_never_triggers_a_checkpoint(tmp_path: Path) -> None:
    """DRY_RUN(샌드박스 포함)은 submit 직전에 이미 return 해서 체크포인트 분기에 안 닿는다."""
    form_url = _write_html(tmp_path / "form.html", _FORM_HTML)
    notifier = _FixedNonceNotifier()
    store = InMemoryCheckpointStore()  # 결정 없음 — 체크포인트를 탔다면 타임아웃으로 실패했을 것
    executor = _executor(tmp_path, notifier, store)
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile={"email": "a@b.com"})

    result = await executor.run(_recipe(form_url), ctx, ExecutionMode.DRY_RUN)

    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is None
    assert notifier.requests == []


async def test_action_level_checkpoint_flag_is_honored(tmp_path: Path) -> None:
    """SUBMIT 이 아닌 액션도 checkpoint=True 면 그 앞에서 멈춘다."""
    form_url = _write_html(tmp_path / "form.html", _FORM_HTML)
    notifier = _FixedNonceNotifier()
    store = InMemoryCheckpointStore()
    executor = _executor(tmp_path, notifier, store)
    await store.record_decision("nonce_fixed", approved=True)
    recipe = AutomationRecipe(
        platform="fixture",
        version=1,
        status="candidate",
        form_hash="h-ab-checkpoint-2",
        actions=[
            Action(type=ActionType.GOTO, value_literal=form_url),
            Action(
                type=ActionType.FILL,
                selector="#email",
                value_ref="profile.email",
                checkpoint=True,
            ),
        ],
        success_signals=["완료"],
    )
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile={"email": "a@b.com"})

    result = await executor.run(recipe, ctx, ExecutionMode.SUPERVISED)

    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert len(notifier.requests) == 1
