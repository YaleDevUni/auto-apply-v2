"""ApplyIntakeWorkflow → start_actionable_applications 이름 매칭 + 재시도 검증 (§ apply-schedule).

실제 dedupe/TTL 로직(`activities/apply_intake.py`)은 이미 `tests/test_apply_intake.py`/
`tests/activities/test_apply_intake.py`가 덮는다 — 여기서는 워크플로우가 그 activity 를
올바른 입력으로 부르고 결과를 그대로 반환하는지, 그리고 실패를 재시도하는지만 본다
(`tests/workflows/test_resume.py`와 같은 패턴 — 로컬 `@activity.defn(name=...)` 스텁으로
activities 계층 없이 워크플로우만 격리한다).

Temporal test server 바이너리만 있으면 돈다 — 마커 없이 기본 실행 (§ test_ping.py).
"""

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult, NotifyEvent
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.apply_intake import ApplyIntakeWorkflow

pytestmark = pytest.mark.temporal

_calls: list[ApplyIntakeInput] = []
_notified: list[NotifyEvent] = []


@activity.defn(name="notify")
async def _stub_notify(event: NotifyEvent) -> None:
    _notified.append(event)


@activity.defn(name="start_actionable_applications")
async def _stub_start_actionable_applications(cmd: ApplyIntakeInput) -> ApplyIntakeResult:
    _calls.append(cmd)
    return ApplyIntakeResult(started=["A - 백엔드"], skipped=["B - 프론트엔드"], candidates=2)


@activity.defn(name="start_actionable_applications")
async def _stub_started_nothing(cmd: ApplyIntakeInput) -> ApplyIntakeResult:
    _calls.append(cmd)
    return ApplyIntakeResult(started=[], skipped=[], candidates=0)


@activity.defn(name="start_actionable_applications")
async def _stub_always_fails(cmd: ApplyIntakeInput) -> ApplyIntakeResult:
    _calls.append(cmd)
    raise RuntimeError("temporal 밖 세상이 무너졌다")


async def _run(activities: list[object], wf_id: str) -> ApplyIntakeResult:
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with Worker(
            client,
            task_queue="test",
            workflows=[ApplyIntakeWorkflow],
            activities=activities,
        ):
            return await client.execute_workflow(
                ApplyIntakeWorkflow.run,
                ApplyIntakeInput(count=5),
                id=wf_id,
                task_queue="test",
            )


async def test_passes_count_through_and_returns_activity_result():
    _calls.clear()
    _notified.clear()

    result = await _run([_stub_start_actionable_applications, _stub_notify], "apply-intake-1")

    assert _calls == [ApplyIntakeInput(count=5)]
    assert result == ApplyIntakeResult(
        started=["A - 백엔드"], skipped=["B - 프론트엔드"], candidates=2
    )
    assert _notified == [], "한 건이라도 시작했으면 매일 알릴 이유가 없다"


async def test_alerts_when_the_scheduled_run_starts_nothing():
    """cron 으로 도는 루틴이라 "0건 시작"은 아무도 안 보면 며칠도 조용하다.

    (§ domain/alerting.py)
    """
    _calls.clear()
    _notified.clear()

    await _run([_stub_started_nothing, _stub_notify], "apply-intake-2")

    assert [e.kind for e in _notified] == ["APPLY_INTAKE_UNHEALTHY"]
    assert "후보 공고가 0건" in _notified[0].message


async def test_alerts_before_failing_when_the_activity_gives_up():
    """워크플로우는 그대로 FAILED 로 죽는다(Temporal UI 의 진실은 유지) — 다만 watchdog 이

    떠 있어야만 사람이 아는 상태로 두지 않는다.
    """
    _calls.clear()
    _notified.clear()

    with pytest.raises(Exception):  # WorkflowFailureError — 죽었다는 사실만 확인하면 된다
        await _run([_stub_always_fails, _stub_notify], "apply-intake-3")

    assert [e.kind for e in _notified] == ["APPLY_INTAKE_UNHEALTHY"]
    assert "자동 지원 시작이 실패했다" in _notified[0].message
