"""JobCollectionWorkflow/ApplyIntakeWorkflow 를 주기 실행하는 Temporal Schedule (ARCHITECTURE.md

§11.2b, §11.2f). 이 모듈은 cron/건수/플랫폼을 스스로 정하지 않는다 — 호출부(`schedule_config.py`)가
DB나 `.env` 시드값에서 읽어 그대로 넘긴다(§ apply-schedule 상단 docstring).

`build_*_schedule`는 순수 함수라 Temporal 없이 바로 검증한다. `ensure_*_schedule`/
`delete_*_schedule`는 실제 Schedule RPC 가 필요한데, time-skipping test server(§ test_ping.py)는
CreateSchedule 을 구현하지 않는다(`RPCError: ... CreateSchedule is unimplemented`) — 그래서
여기서만 `WorkflowEnvironment.start_local()`(풀 dev server)를 쓴다. 최초 실행 시 별도
바이너리를 내려받고 기동에 시간이 걸린다.
"""

import pytest
from temporalio.client import ScheduleOverlapPolicy
from temporalio.testing import WorkflowEnvironment

from auto_apply.contracts.dto import ApplyIntakeInput
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.schedule import (
    APPLY_INTAKE_SCHEDULE_ID,
    JOB_COLLECTION_SCHEDULE_ID,
    build_apply_intake_schedule,
    build_job_collection_schedule,
    delete_apply_intake_schedule,
    delete_job_collection_schedule,
    ensure_apply_intake_schedule,
    ensure_job_collection_schedule,
)
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_DEFAULT


def test_build_schedule_uses_given_cron_and_platforms():
    schedule = build_job_collection_schedule("0 9 * * *", ["wanted", "saramin"])

    assert schedule.spec.cron_expressions == ["0 9 * * *"]
    assert schedule.action.task_queue == QUEUE_DEFAULT
    assert schedule.action.args == [CollectJobsInput(platforms=["wanted", "saramin"])]
    # 겹쳐 돌리면 같은 공고를 동시에 upsert할 수 있어 SKIP으로 막는다 (§11.2b).
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


@pytest.mark.integration
async def test_ensure_creates_then_updates_and_delete_removes_it():
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client = env.client

        assert await ensure_job_collection_schedule(client, "0 9 * * *", ["wanted"]) == "created"
        # 재실행해도 두 번째부터는 업데이트다 — 몇 번을 실행해도 안전해야 한다.
        assert await ensure_job_collection_schedule(client, "0 9 * * *", ["wanted"]) == "updated"

        outcome = await ensure_job_collection_schedule(
            client, "0 9 * * *", ["wanted", "saramin", "jasoseol"]
        )
        assert outcome == "updated"

        handle = client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID)
        desc = await handle.describe()
        [payload] = desc.schedule.action.args
        assert b"jasoseol" in payload.data

        await delete_job_collection_schedule(client)
        with pytest.raises(Exception):  # RPCError: schedule not found
            await handle.describe()


@pytest.mark.integration
async def test_ensure_update_preserves_paused_state():
    """시각/건수만 바꾸는 update가 꺼둔 Schedule을 조용히 다시 켜면 안 된다(§ apply-schedule).

    `Schedule(state=...)`의 기본값은 unpaused라 매번 새로 지은 Schedule 객체로 그대로 update
    하면 이 회귀가 재현된다.
    """
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client = env.client

        await ensure_job_collection_schedule(client, "0 9 * * *", ["wanted"])
        handle = client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID)
        await handle.pause(note="test")
        assert (await handle.describe()).schedule.state.paused is True

        await ensure_job_collection_schedule(client, "0 11 * * *", ["wanted"])

        assert (await handle.describe()).schedule.state.paused is True


def test_build_apply_intake_schedule_uses_given_cron_and_count():
    schedule = build_apply_intake_schedule("0 10 * * *", 5)

    assert schedule.spec.cron_expressions == ["0 10 * * *"]
    assert schedule.action.task_queue == QUEUE_DEFAULT
    assert schedule.action.args == [ApplyIntakeInput(count=5)]
    # job-collection Schedule 과 같은 이유(§11.2b) — 겹쳐 돌 이유가 없다.
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


@pytest.mark.integration
async def test_ensure_apply_intake_creates_then_updates_and_delete_removes_it():
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client = env.client

        assert await ensure_apply_intake_schedule(client, "0 10 * * *", 3) == "created"
        assert await ensure_apply_intake_schedule(client, "0 10 * * *", 3) == "updated"
        assert await ensure_apply_intake_schedule(client, "0 10 * * *", 7) == "updated"

        handle = client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID)
        desc = await handle.describe()
        [payload] = desc.schedule.action.args
        assert b'"count":7' in payload.data

        await delete_apply_intake_schedule(client)
        with pytest.raises(Exception):  # RPCError: schedule not found
            await handle.describe()
