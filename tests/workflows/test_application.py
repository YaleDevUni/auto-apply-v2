"""ApplicationWorkflow 테스트 (ARCHITECTURE.md §2.2).

시간 건너뛰기(time-skipping) 환경을 쓰므로 72시간 승인 타임아웃과 30일 뒤 예약도 즉시 검증된다.
이게 durable timer 를 테스트로 증명할 수 있는 이유다.
"""

from datetime import timedelta

import pytest
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.contracts.dto import (
    ApproveSignal,
    RejectSignal,
    RescheduleSignal,
    StartApplication,
)
from auto_apply.domain.enums import ApplicationState
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_BROWSER, QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.resume import ResumeWorkflow
from tests.conftest import JOB_URL, Harness

pytestmark = pytest.mark.integration

APP_ID = "app_1"


def _cmd(**kw: object) -> StartApplication:
    base: dict[str, object] = {
        "application_id": APP_ID,
        "user_id": "u1",
        "job_url": JOB_URL,
        "dry_run_only": True,
    }
    base.update(kw)
    return StartApplication.model_validate(base)


async def _start(
    client: Client, cmd: StartApplication
) -> WorkflowHandle[ApplicationWorkflow, object]:
    return await client.start_workflow(
        ApplicationWorkflow.run,
        cmd,
        id=f"application-{cmd.application_id}",
        task_queue=QUEUE_DEFAULT,
    )


class _Workers:
    """세 큐에 각각 워커를 띄운다 — 큐 라우팅이 실제로 맞는지도 같이 검증된다 (§1)."""

    def __init__(self, client: Client, harness: Harness) -> None:
        self._client = client
        self._acts = harness.activities()
        self._workers: list[Worker] = []

    async def __aenter__(self) -> "_Workers":
        specs = [
            (QUEUE_DEFAULT, [ApplicationWorkflow]),
            (QUEUE_AI, [ResumeWorkflow]),
            (QUEUE_BROWSER, []),
        ]
        for queue, wfs in specs:
            # max_cached_workflows=0 → sticky execution 비활성화.
            # 워커가 죽어도 서버가 죽은 워커의 sticky 큐로 라우팅해서 재시작 테스트가 멈춘다.
            w = Worker(
                self._client,
                task_queue=queue,
                workflows=wfs,
                activities=self._acts,
                max_cached_workflows=0,
            )
            await w.__aenter__()
            self._workers.append(w)
        return self

    async def __aexit__(self, *exc: object) -> None:
        for w in reversed(self._workers):
            await w.__aexit__(None, None, None)  # type: ignore[arg-type]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


# ─────────────────────────── 승인 경로 ───────────────────────────
async def test_approve_then_scheduled_execution_completes(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

        run_at = (await env.get_current_time()) + timedelta(days=30)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=run_at))

        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert "dry_run" in result.reason
    # 상태 전이가 projection 으로 남아야 한다 (§4.1)
    assert h.states(APP_ID) == [
        "evaluating",
        "generating_resume",
        "rendering_pdf",
        "awaiting_approval",
        "scheduled",
        "executing",
        "completed",
    ]


async def test_reject_signal_ends_as_rejected(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.reject, RejectSignal(reason="회사 부적합"))
        result = await handle.result()

    assert result.state is ApplicationState.REJECTED
    assert result.reason == "회사 부적합"
    assert "executing" not in h.states(APP_ID), "거절했는데 실행 단계로 갔다"


async def test_duplicate_approval_is_idempotent(env: WorkflowEnvironment):
    """Telegram 버튼은 두 번 눌린다. 첫 승인만 반영되어야 한다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        first = (await env.get_current_time()) + timedelta(days=10)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=first))
        # 두 번째 승인은 무시되어야 한다 (거절로 뒤집히지도 않아야 한다)
        await handle.signal(ApplicationWorkflow.reject, RejectSignal(reason="늦은 거절"))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED


# ─────────────────────────── 타이머 / 스케줄 ───────────────────────────
async def test_approval_timeout_expires(env: WorkflowEnvironment):
    """72시간 무응답 → expired. 무한 대기 워크플로우를 만들지 않는다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(approval_timeout_hours=72))
        result = await handle.result()  # time-skipping 이 72시간을 즉시 통과시킨다

    assert result.state is ApplicationState.EXPIRED
    assert h.states(APP_ID)[-1] == "expired"


async def test_reschedule_while_waiting_moves_the_timer(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

        now = await env.get_current_time()
        original = now + timedelta(days=30)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=original))
        await _wait_state(handle, ApplicationState.SCHEDULED)

        # 재조정 전: 워크플로우는 원래 목표를 들고 대기 중이다
        assert (await handle.query(ApplicationWorkflow.state)).scheduled_at == original

        moved = now + timedelta(days=1)
        await handle.signal(ApplicationWorkflow.reschedule, RescheduleSignal(scheduled_at=moved))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    # 대기 중에 타이머 목표가 실제로 교체됐다 (sleep 이었다면 signal 이 먹지 않는다)
    # projection 은 (run_id, state) 로 멱등 upsert 되므로 행 수가 아니라 값을 본다 (§4.1)
    row = h.row(APP_ID, "scheduled")
    assert row is not None and row.scheduled_at == moved


async def test_cancel_while_scheduled(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        now = await env.get_current_time()
        await handle.signal(
            ApplicationWorkflow.approve, ApproveSignal(scheduled_at=now + timedelta(days=30))
        )
        await _wait_state(handle, ApplicationState.SCHEDULED)
        await handle.signal(ApplicationWorkflow.cancel)
        result = await handle.result()

    assert result.state is ApplicationState.CANCELLED
    assert "executing" not in h.states(APP_ID)


# ─────────────────────── 실패 / 안전장치 ───────────────────────
async def test_ineligible_job_never_reaches_execution(env: WorkflowEnvironment):
    h = Harness(eligible=False, reject_reason="경력 요건 미달")
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        result = await handle.result()

    assert result.state is ApplicationState.REJECTED
    assert result.reason == "경력 요건 미달"
    assert h.states(APP_ID) == ["evaluating", "rejected"]


async def test_recipe_failure_goes_to_needs_human(env: WorkflowEnvironment):
    """DOM 변경 상황. M4 까지는 사람에게 넘긴다 — 절대 재시도로 밀어붙이지 않는다."""
    h = Harness(fail_selectors=frozenset({"#email"}))
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.NEEDS_HUMAN
    assert "RecipeExecutionError" in result.reason
    assert h.states(APP_ID)[-1] == "needs_human"


async def test_dry_run_never_submits(env: WorkflowEnvironment):
    """DRY_RUN_ONLY=true 면 submit 이 실행되지 않는다 (§9.5)."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=True))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert result.submitted_at is None, "dry_run 인데 제출 시각이 기록됐다"


async def test_live_mode_submits_and_verifies(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=False))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert result.submitted_at is not None
    assert "verifying" in h.states(APP_ID)


async def test_verification_failure_goes_to_needs_human(env: WorkflowEnvironment):
    """제출은 됐는데 확인이 안 되면 성공으로 처리하지 않는다 (§5 부분 제출 위험)."""
    h = Harness(verified=False)
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=False))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.NEEDS_HUMAN
    assert "제출 확인 실패" in result.reason


async def test_unknown_platform_url_is_refused(env: WorkflowEnvironment):
    """allowlist 밖 도메인에는 자동화를 돌리지 않는다 (§3)."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(job_url="https://unknown.example.com/x"))
        with pytest.raises(WorkflowFailureError):
            await handle.result()


# ─────────────────────────── helper ───────────────────────────
async def _wait_state(
    handle: WorkflowHandle[ApplicationWorkflow, object], target: ApplicationState
) -> None:
    """워크플로우 query 로 상태를 기다린다 — DB 를 폴링하지 않는다 (§4.1)."""
    for _ in range(200):
        view = await handle.query(ApplicationWorkflow.state)
        if view.state is target:
            return
        await _tick()
    raise AssertionError(f"상태가 {target} 에 도달하지 않았다")


async def _tick() -> None:
    import asyncio

    await asyncio.sleep(0.05)
