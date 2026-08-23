"""resend_pending_decision/resend_all_pending_decisions 도구 (telegram/_agent_tools_resend.py).

resend_pending_decision(application_id 지정) 자체의 배선 검증은 test_agent.py 에 이미 있다
(파일을 옮기기 전부터 있던 테스트라 그대로 둔다). 여기는 새로 추가한
resend_all_pending_decisions 만 다룬다.
"""

from dataclasses import dataclass, replace

from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import DecisionRequest, PendingDecisionView
from auto_apply.telegram._agent_tools_resend import _resend_all_pending_decisions
from auto_apply.telegram.agent import handle_chat
from tests.conftest import Harness
from tests.telegram.test_agent import _FakeNotifier


def _req(application_id: str, title: str = "회사 / 직무 지원 승인") -> DecisionRequest:
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
    def __init__(self, view: PendingDecisionView) -> None:
        self._view = view

    async def query(self, _fn: object) -> PendingDecisionView:
        return self._view


class _FakeClient:
    def __init__(self, rows: dict[str, PendingDecisionView]) -> None:
        self._rows = rows

    def list_workflows(self, _query: str) -> object:
        async def _iter():
            for app_id in self._rows:
                yield _FakeExecution(id=f"application-{app_id}", run_id="r1")

        return _iter()

    def get_workflow_handle(self, workflow_id: str, *, run_id: str) -> _FakeHandle:
        app_id = workflow_id.removeprefix("application-")
        return _FakeHandle(self._rows[app_id])


def _container() -> Container:
    return Harness().container(settings=Settings(storage="memory", llm_provider="stub"))


async def test_resend_all_pending_decisions_resends_every_pending_one():
    notifier = _FakeNotifier()
    c = _container()
    client = _FakeClient(
        {
            "app_1": PendingDecisionView(has_pending=True, nonce="n1", request=_req("app_1")),
            "app_2": PendingDecisionView(has_pending=False),
            "app_3": PendingDecisionView(has_pending=True, nonce="n3", request=_req("app_3")),
        }
    )
    c = replace(c, notifier=notifier)

    message = await _resend_all_pending_decisions({}, c, client)

    assert sorted(notifier.resent) == [("app_1", "n1"), ("app_3", "n3")]
    assert "app_1" in message
    assert "app_3" in message
    assert "app_2" not in message


async def test_resend_all_pending_decisions_reports_when_nothing_pending():
    notifier = _FakeNotifier()
    c = replace(_container(), notifier=notifier)
    client = _FakeClient({})

    message = await _resend_all_pending_decisions({}, c, client)

    assert notifier.resent == []
    assert "없습니다" in message


async def test_resend_all_pending_decisions_tool_is_wired_into_chat_agent():
    notifier = _FakeNotifier()
    stub = StubLLM(
        payloads=[
            {"action": "call_tool", "tool": "resend_all_pending_decisions", "tool_args": {}},
            {"action": "respond", "response": "대기 중이던 승인 버튼을 다시 보냈어요."},
        ]
    )
    c = replace(_container(), llm=stub, chat_llm=stub, notifier=notifier)
    client = _FakeClient(
        {"app_1": PendingDecisionView(has_pending=True, nonce="n1", request=_req("app_1"))}
    )

    await handle_chat("승인 기다리는 거 다시 보여줘", c, client)

    assert notifier.resent == [("app_1", "n1")]
    assert [e.message for e in notifier.notified] == ["대기 중이던 승인 버튼을 다시 보냈어요."]
