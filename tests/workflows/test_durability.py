"""M1 완료 조건: 워커를 죽였다 살려도 승인 대기와 예약이 살아있다.

이게 Temporal 을 도입한 유일한 이유이므로, "코드가 돌아간다" 대신 이걸 테스트한다.
워커를 완전히 종료(async with 블록 종료)한 뒤 재기동해서 같은 워크플로우가 이어지는지 본다.
"""

from datetime import timedelta

import pytest
from temporalio.testing import WorkflowEnvironment

from auto_apply.contracts.dto import ApproveSignal
from auto_apply.domain.enums import ApplicationState
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.application import ApplicationWorkflow
from tests.conftest import Harness
from tests.workflows.test_application import APP_ID, _cmd, _start, _wait_state, _Workers

pytestmark = pytest.mark.integration


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


async def test_approval_signal_survives_full_worker_restart(env: WorkflowEnvironment):
    """워커가 하나도 없는 동안 도착한 승인이 유실되지 않는다."""
    rows: dict = {}

    # ① 워커 A: 승인 대기까지 진행
    async with _Workers(env.client, Harness(rows=rows)):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
    # ← 여기서 세 큐의 워커가 모두 종료된다

    # ② 워커가 없는 상태에서 승인 (서버가 signal 을 보관한다)
    run_at = (await env.get_current_time()) + timedelta(days=7)
    await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=run_at))

    # ③ 워커 B 기동 → 중단 지점부터 이어서 완료
    h2 = Harness(rows=rows)
    async with _Workers(env.client, h2):
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    # 재시작 전 상태와 재시작 후 상태가 하나의 이력으로 이어진다
    assert h2.states(APP_ID) == [
        "evaluating",
        "generating_resume",
        "rendering_pdf",
        "awaiting_approval",
        "scheduled",
        "executing",
        "completed",
    ]


async def test_pending_timer_survives_worker_restart(env: WorkflowEnvironment):
    """예약 대기 중에 워커를 죽여도 타이머가 살아있다 — durable timer 의 핵심."""
    rows: dict = {}

    # ① 승인까지 받고 30일 뒤로 예약한 상태에서 워커를 내린다
    async with _Workers(env.client, Harness(rows=rows)):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        run_at = (await env.get_current_time()) + timedelta(days=30)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=run_at))
        await _wait_state(handle, ApplicationState.SCHEDULED)
        view = await handle.query(ApplicationWorkflow.state)
        assert view.scheduled_at == run_at

    # ② 워커 없이 시간이 흐른다 (실제 환경의 "서버 재배포" 구간)
    #    ③ 새 워커가 붙으면 예약 시각에 맞춰 실행이 재개된다
    h2 = Harness(rows=rows)
    async with _Workers(env.client, h2):
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    row = h2.row(APP_ID, "scheduled")
    assert row is not None and row.scheduled_at == run_at, "재시작 후 예약 시각이 유실됐다"


async def test_workflow_state_is_queryable_after_restart(env: WorkflowEnvironment):
    """상태의 원본은 DB 가 아니라 워크플로우다 (§4.1). 재시작 후에도 query 가 답한다."""
    rows: dict = {}

    async with _Workers(env.client, Harness(rows=rows)):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

    async with _Workers(env.client, Harness(rows=rows)):
        view = await handle.query(ApplicationWorkflow.state)
        assert view.state is ApplicationState.AWAITING_APPROVAL
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        await handle.result()
