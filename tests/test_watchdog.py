"""watchdog.py — 워크플로우 능동 감시 (workflow-failure-visibility-backlog §3).

순수 함수(`build_query`/`to_hit`/`format_message`)는 인프라 없이 검증한다. `poll_once`의
실제 동작(visibility 조회 + 중복 억제)은 real Temporal dev server가 필요해서
`@pytest.mark.integration`이다 — `test_schedule.py`와 같은 이유로 시간 스킵 서버가 아니라
`start_local()`을 쓴다(Standard SQL visibility의 `ExecutionStatus`/`CloseTime` 필터가
time-skipping 서버에도 있는지 보증되지 않는다 — 실제 동작을 보증하는 쪽을 택했다).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.watchdog import (
    WatchdogHit,
    blind_alert,
    build_query,
    format_message,
    poll_once,
    to_hit,
)
from tests.fixtures.watchdog_fail_workflow import AlwaysFailWorkflow
from tests.ports.test_notifier_contract import RecordingNotifier


def test_build_query_filters_bad_statuses_and_close_time():
    since = datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC)

    query = build_query(since)

    assert "ExecutionStatus IN ('Failed', 'Terminated', 'TimedOut')" in query
    assert "CloseTime > '2026-08-20T00:00:00+00:00'" in query


def test_format_message_includes_id_and_status():
    hit = WatchdogHit(
        workflow_id="application-42",
        run_id="run-1",
        status="FAILED",
        close_time=datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC),
    )

    message = format_message(hit)

    assert "application-42" in message
    assert "FAILED" in message
    assert "2026-08-20T01:02:03" in message


def test_blind_alert_fires_once_exactly_at_the_threshold():
    """계속 실패하는 동안 매 주기 알리면 그게 소음이다 — 임계치에 닿은 순간만 알린다."""
    assert blind_alert(1, 3, "connection refused") is None
    assert blind_alert(2, 3, "connection refused") is None

    message = blind_alert(3, 3, "connection refused")

    assert message is not None
    assert "connection refused" in message

    assert blind_alert(4, 3, "connection refused") is None


@dataclass
class _FakeExecution:
    id: str
    run_id: str
    status: object
    close_time: datetime | None


class _FakeStatus:
    def __init__(self, name: str) -> None:
        self.name = name


def test_to_hit_maps_unknown_status_when_status_missing():
    hit = to_hit(_FakeExecution(id="wf-1", run_id="run-1", status=None, close_time=None))

    assert hit.status == "UNKNOWN"


async def test_poll_once_does_not_renotify_workflow_already_seen():
    """중복 억제는 (workflow_id, run_id) 로 한다 — `seen`에 있으면 다시 안 부른다."""
    hit_key = ("application-1", "run-1")

    class _OneHitClient:
        def list_workflows(self, query: str) -> object:
            async def _iter():
                yield _FakeExecution(
                    id="application-1",
                    run_id="run-1",
                    status=_FakeStatus("FAILED"),
                    close_time=datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC),
                )

            return _iter()

    notifier = RecordingNotifier()
    since = datetime(2026, 8, 19, tzinfo=UTC)

    seen = {hit_key}
    await poll_once(_OneHitClient(), notifier, since, seen)  # type: ignore[arg-type]

    assert notifier.events == []  # 이미 seen 에 있으니 재알림 없음


async def test_poll_once_notifies_and_advances_watermark_on_new_hit():
    class _OneHitClient:
        def list_workflows(self, query: str) -> object:
            async def _iter():
                yield _FakeExecution(
                    id="application-2",
                    run_id="run-2",
                    status=_FakeStatus("TERMINATED"),
                    close_time=datetime(2026, 8, 20, 5, 0, 0, tzinfo=UTC),
                )

            return _iter()

    notifier = RecordingNotifier()
    since = datetime(2026, 8, 19, tzinfo=UTC)
    seen: set[tuple[str, str]] = set()

    watermark = await poll_once(_OneHitClient(), notifier, since, seen)  # type: ignore[arg-type]

    assert len(notifier.events) == 1
    assert notifier.events[0].kind == "WORKFLOW_UNHEALTHY"
    assert "application-2" in notifier.events[0].message
    assert ("application-2", "run-2") in seen
    assert watermark == datetime(2026, 8, 20, 5, 0, 0, tzinfo=UTC)


@pytest.mark.integration
async def test_poll_once_finds_a_real_failed_workflow_via_visibility_api():
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        since = datetime.now(UTC) - timedelta(minutes=5)

        async with Worker(
            client, task_queue="watchdog-test", workflows=[AlwaysFailWorkflow], activities=[]
        ):
            with pytest.raises(Exception):  # WorkflowFailureError
                await client.execute_workflow(
                    AlwaysFailWorkflow.run,
                    id="watchdog-fail-1",
                    task_queue="watchdog-test",
                )

        notifier = RecordingNotifier()
        seen: set[tuple[str, str]] = set()

        # visibility 색인에는 약간의 지연이 있을 수 있어 짧게 재시도한다.
        found = False
        for _ in range(20):
            await poll_once(client, notifier, since, seen)
            if notifier.events:
                found = True
                break
        assert found, "실패한 워크플로우를 visibility API 로 못 찾았다"
        assert "watchdog-fail-1" in notifier.events[0].message

        # 같은 워크플로우를 다시 폴링해도 중복 알림은 없다.
        events_before = len(notifier.events)
        await poll_once(client, notifier, since, seen)
        assert len(notifier.events) == events_before
