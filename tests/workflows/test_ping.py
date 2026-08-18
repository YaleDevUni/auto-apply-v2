"""M0 smoke: workflow → activity stub 이름 매칭이 실제로 동작하는지 (§11.3).

@pytest.mark.integration — Temporal test server 바이너리가 필요하다.
  make up 없이도 돌지만 최초 실행 시 서버 바이너리를 내려받는다.
"""

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.activities.ping import PingActivities
from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.workflows.ping import PingWorkflow

pytestmark = pytest.mark.integration


async def test_ping_workflow_resolves_activity_by_name():
    store = InMemoryBlobStore()
    acts = PingActivities(SystemClock(), store)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        client: Client = env.client
        async with Worker(
            client, task_queue="test", workflows=[PingWorkflow], activities=acts.all()
        ):
            result = await client.execute_workflow(
                PingWorkflow.run, "hello", id="ping-1", task_queue="test"
            )
    assert result.startswith("pong:hello@")
    assert await store.exists("smoke/ping-1.txt")
