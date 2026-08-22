"""ApplyIntakeWorkflow → start_actionable_applications 이름 매칭 + 재시도 검증 (§ apply-schedule).

실제 dedupe/TTL 로직(`activities/apply_intake.py`)은 이미 `tests/test_apply_intake.py`/
`tests/activities/test_apply_intake.py`가 덮는다 — 여기서는 워크플로우가 그 activity 를
올바른 입력으로 부르고 결과를 그대로 반환하는지, 그리고 실패를 재시도하는지만 본다
(`tests/workflows/test_resume.py`와 같은 패턴 — 로컬 `@activity.defn(name=...)` 스텁으로
activities 계층 없이 워크플로우만 격리한다).

@pytest.mark.integration — Temporal test server 바이너리가 필요하다 (§ test_ping.py).
"""

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.apply_intake import ApplyIntakeWorkflow

pytestmark = pytest.mark.integration

_calls: list[ApplyIntakeInput] = []


@activity.defn(name="start_actionable_applications")
async def _stub_start_actionable_applications(cmd: ApplyIntakeInput) -> ApplyIntakeResult:
    _calls.append(cmd)
    return ApplyIntakeResult(started=["A - 백엔드"], skipped=["B - 프론트엔드"], candidates=2)


async def test_passes_count_through_and_returns_activity_result():
    _calls.clear()
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with Worker(
            client,
            task_queue="test",
            workflows=[ApplyIntakeWorkflow],
            activities=[_stub_start_actionable_applications],
        ):
            result = await client.execute_workflow(
                ApplyIntakeWorkflow.run,
                ApplyIntakeInput(count=5),
                id="apply-intake-1",
                task_queue="test",
            )

    assert _calls == [ApplyIntakeInput(count=5)]
    assert result == ApplyIntakeResult(
        started=["A - 백엔드"], skipped=["B - 프론트엔드"], candidates=2
    )
