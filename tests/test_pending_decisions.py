"""pending_decisions.py — 승인 대기 중인 지원 건 일괄 조회/재전송.

`find_pending_decisions`/`resend_all`은 fake client 로 빠르게 검증한다(watchdog.py의
`poll_once` 유닛 테스트와 같은 패턴). `WorkflowType`/`ExecutionStatus` 필터 자체가 실제
Temporal visibility API 에서 통하는지는 fake로 증명이 안 되므로, 실제 `ApplicationWorkflow`를
AWAITING_APPROVAL 까지 띄운 뒤 visibility로 찾아내는 통합 테스트를 하나 더 둔다(watchdog.py의
`test_poll_once_finds_a_real_failed_workflow_via_visibility_api`와 같은 이유 — Standard SQL
visibility가 time-skipping 서버에도 있는지 보증되지 않아 `start_local()`을 쓴다).
"""

from dataclasses import dataclass

import pytest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from auto_apply.contracts.dto import DecisionRequest, PendingDecisionView, RejectSignal
from auto_apply.domain.enums import ApplicationState
from auto_apply.pending_decisions import PendingDecision, find_pending_decisions, resend_all
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.application import ApplicationWorkflow
from tests.conftest import Harness
from tests.workflows.test_application import APP_ID, _cmd, _start, _tick, _wait_state, _Workers


def _req(application_id: str = "app_1", title: str = "회사 / 직무 지원 승인") -> DecisionRequest:
    return DecisionRequest(
        application_id=application_id,
        workflow_id=f"application-{application_id}",
        title=title,
        summary="https://fixture.local/jobs/1",
    )


@dataclass
class _FakeExecution:
    id: str
    run_id: str


class _FakeHandle:
    def __init__(self, view: PendingDecisionView | None = None, *, error: Exception | None = None):
        self._view = view
        self._error = error

    async def query(self, _fn: object) -> PendingDecisionView:
        if self._error is not None:
            raise self._error
        assert self._view is not None
        return self._view


class _FakeClient:
    def __init__(self, executions: list[_FakeExecution], handles: dict[str, _FakeHandle]) -> None:
        self._executions = executions
        self._handles = handles

    def list_workflows(self, _query: str) -> object:
        async def _iter():
            for e in self._executions:
                yield e

        return _iter()

    def get_workflow_handle(self, workflow_id: str, *, run_id: str) -> _FakeHandle:
        return self._handles[workflow_id]


class _FakeNotifier:
    def __init__(self) -> None:
        self.resent: list[tuple[DecisionRequest, str]] = []

    async def resend_decision(self, request: DecisionRequest, nonce: str) -> None:
        self.resent.append((request, nonce))


async def test_find_pending_decisions_keeps_only_the_ones_with_a_pending_nonce():
    client = _FakeClient(
        executions=[
            _FakeExecution(id="application-app_1", run_id="r1"),
            _FakeExecution(id="application-app_2", run_id="r2"),
        ],
        handles={
            "application-app_1": _FakeHandle(
                PendingDecisionView(has_pending=True, nonce="n1", request=_req("app_1"))
            ),
            "application-app_2": _FakeHandle(PendingDecisionView(has_pending=False)),
        },
    )

    found = await find_pending_decisions(client)  # type: ignore[arg-type]

    assert found == [PendingDecision(application_id="app_1", nonce="n1", request=_req("app_1"))]


async def test_find_pending_decisions_skips_a_workflow_whose_query_fails():
    """query 시점에 이미 끝났거나 워커가 안 뜬 경우 — 그 건만 건너뛰고 나머지는 계속 본다."""
    client = _FakeClient(
        executions=[
            _FakeExecution(id="application-app_1", run_id="r1"),
            _FakeExecution(id="application-app_2", run_id="r2"),
        ],
        handles={
            "application-app_1": _FakeHandle(
                error=RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
            ),
            "application-app_2": _FakeHandle(
                PendingDecisionView(has_pending=True, nonce="n2", request=_req("app_2"))
            ),
        },
    )

    found = await find_pending_decisions(client)  # type: ignore[arg-type]

    assert found == [PendingDecision(application_id="app_2", nonce="n2", request=_req("app_2"))]


async def test_resend_all_resends_every_pending_decision_and_returns_them():
    req1, req2 = _req("app_1", "회사1 / 직무1 지원 승인"), _req("app_2", "회사2 / 직무2 지원 승인")
    client = _FakeClient(
        executions=[
            _FakeExecution(id="application-app_1", run_id="r1"),
            _FakeExecution(id="application-app_2", run_id="r2"),
        ],
        handles={
            "application-app_1": _FakeHandle(
                PendingDecisionView(has_pending=True, nonce="n1", request=req1)
            ),
            "application-app_2": _FakeHandle(
                PendingDecisionView(has_pending=True, nonce="n2", request=req2)
            ),
        },
    )
    notifier = _FakeNotifier()

    pending = await resend_all(client, notifier)  # type: ignore[arg-type]

    # 재전송은 원래 DecisionRequest 를 그대로 다시 실어 보낸다 — 회사/직무/공고 링크/PDF/
    # 주의사항까지 원래 메시지와 동일하게 렌더링되도록(`TelegramNotifier.resend_decision`).
    assert notifier.resent == [(req1, "n1"), (req2, "n2")]
    assert pending == [
        PendingDecision(application_id="app_1", nonce="n1", request=req1),
        PendingDecision(application_id="app_2", nonce="n2", request=req2),
    ]
    assert pending[0].label == "회사1 / 직무1 지원 승인"


async def test_resend_all_does_nothing_when_nothing_is_pending():
    client = _FakeClient(executions=[], handles={})
    notifier = _FakeNotifier()

    pending = await resend_all(client, notifier)  # type: ignore[arg-type]

    assert pending == []
    assert notifier.resent == []


async def test_pending_decision_label_falls_back_to_application_id_without_a_request():
    """request 가 없는 경우(이론상만) 는 여전히 application_id 로 fallback한다."""
    assert PendingDecision(application_id="app_9", nonce="n9").label == "app_9"


@pytest.mark.temporal
async def test_find_pending_decisions_discovers_a_real_awaiting_application():
    async with await WorkflowEnvironment.start_local(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        h = Harness()
        async with _Workers(client, h):
            handle = await _start(client, _cmd())
            await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

            found: list[PendingDecision] = []
            for _ in range(30):
                found = await find_pending_decisions(client)
                if any(p.application_id == APP_ID for p in found):
                    break
                await _tick()  # visibility 색인에 약간의 지연이 있을 수 있어 짧게 재시도
            assert any(p.application_id == APP_ID for p in found), (
                "실행 중인 ApplicationWorkflow 를 visibility API 로 못 찾았다"
            )
            match = next(p for p in found if p.application_id == APP_ID)
            assert match.request is not None
            assert "Fixture Inc." in match.request.title

            await handle.signal(ApplicationWorkflow.reject, RejectSignal())
            await handle.result()
