"""JobCollectionWorkflow 를 주기 실행하는 Schedule (ARCHITECTURE.md §11.2b).

`build_job_collection_schedule`는 순수 함수라 Temporal 없이 바로 검증한다.
`ensure_job_collection_schedule`/`delete_job_collection_schedule`는 실제 Schedule RPC 가
필요한데, time-skipping test server(§ test_ping.py)는 CreateSchedule 을 구현하지 않는다
(`RPCError: ... CreateSchedule is unimplemented`) — 그래서 여기서만
`WorkflowEnvironment.start_local()`(풀 dev server)를 쓴다. 최초 실행 시 별도 바이너리를
내려받고 기동에 시간이 걸린다.
"""

import pytest
from temporalio.client import ScheduleOverlapPolicy
from temporalio.testing import WorkflowEnvironment

from auto_apply.config import Settings
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.schedule import (
    JOB_COLLECTION_SCHEDULE_ID,
    build_job_collection_schedule,
    delete_job_collection_schedule,
    ensure_job_collection_schedule,
)
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_DEFAULT


def _cfg(**overrides: object) -> Settings:
    base = {"job_collection_cron": "0 9 * * *", "job_collection_platforms": "wanted,saramin"}
    return Settings(**{**base, **overrides})


def test_build_schedule_uses_configured_cron_and_platforms():
    schedule = build_job_collection_schedule(_cfg())

    assert schedule.spec.cron_expressions == ["0 9 * * *"]
    assert schedule.action.task_queue == QUEUE_DEFAULT
    assert schedule.action.args == [CollectJobsInput(platforms=["wanted", "saramin"])]
    # 겹쳐 돌리면 같은 공고를 동시에 upsert할 수 있어 SKIP으로 막는다 (§11.2b).
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


def test_build_schedule_strips_whitespace_in_platform_list():
    schedule = build_job_collection_schedule(
        _cfg(job_collection_platforms="wanted, saramin , jasoseol")
    )

    assert schedule.action.args == [CollectJobsInput(platforms=["wanted", "saramin", "jasoseol"])]


@pytest.mark.integration
async def test_ensure_creates_then_updates_and_delete_removes_it():
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client = env.client
        cfg = _cfg()

        assert await ensure_job_collection_schedule(client, cfg) == "created"
        # 재실행해도 두 번째부터는 업데이트다 — 몇 번을 실행해도 안전해야 한다.
        assert await ensure_job_collection_schedule(client, cfg) == "updated"

        updated_cfg = cfg.model_copy(update={"job_collection_platforms": "wanted,saramin,jasoseol"})
        assert await ensure_job_collection_schedule(client, updated_cfg) == "updated"

        handle = client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID)
        desc = await handle.describe()
        [payload] = desc.schedule.action.args
        assert b"jasoseol" in payload.data

        await delete_job_collection_schedule(client)
        with pytest.raises(Exception):  # RPCError: schedule not found
            await handle.describe()
