"""telegram/agent.py — ReAct 루프 + 도구 레지스트리.

`StubLLM(payloads=[...])`로 매 턴의 `AgentStep`을 스크립트하고, 실제 Temporal/네트워크 없이
도구가 올바른 관찰 결과를 만들고 최종 답이 notifier 로 나가는지만 본다.
"""

import dataclasses

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.config import Settings
from auto_apply.contracts.dto import NotifyEvent, PendingDecisionView, PersistState
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.chat_agent import MAX_STEPS
from auto_apply.domain.enums import ApplicationState
from auto_apply.telegram.agent import handle_chat
from tests.conftest import Harness


class _FakeNotifier:
    def __init__(self) -> None:
        self.notified: list[NotifyEvent] = []
        self.resent: list[tuple[str, str]] = []

    async def notify(self, event: NotifyEvent) -> None:
        self.notified.append(event)

    async def resend_decision(self, application_id: str, nonce: str) -> None:
        self.resent.append((application_id, nonce))


class _FakeHandle:
    def __init__(
        self, *, view: PendingDecisionView | None = None, error: Exception | None = None
    ) -> None:
        self._view = view
        self._error = error

    async def query(self, _fn: object) -> PendingDecisionView:
        if self._error is not None:
            raise self._error
        assert self._view is not None
        return self._view


class _FakeClient:
    def __init__(self, handle: _FakeHandle | None = None) -> None:
        self._handle = handle or _FakeHandle()
        self.started: list[str] = []

    def get_workflow_handle(self, _wf_id: str) -> _FakeHandle:
        return self._handle

    async def start_workflow(self, _fn, _cmd, *, id, task_queue, id_reuse_policy):
        self.started.append(id)


def _container(
    payloads: list[dict[str, object]],
    *,
    notifier: _FakeNotifier | None = None,
    harness: Harness | None = None,
) -> object:
    h = harness or Harness()
    h.rows["app_1"] = [
        PersistState(
            application_id="app_1",
            workflow_run_id="run_1",
            state=ApplicationState.AWAITING_APPROVAL,
        )
    ]
    base = h.container(settings=Settings(storage="memory", llm_provider="stub"))
    stub = StubLLM(payloads=payloads)
    # handle_chat 은 c.chat_llm 을 쓴다(c.llm 은 이력서 생성용) — 둘 다 같은 스크립트로 바꿔서
    # 테스트가 어느 필드를 참조하는지 신경 안 써도 되게 한다.
    return dataclasses.replace(base, llm=stub, chat_llm=stub, notifier=notifier or _FakeNotifier())


async def test_read_tool_call_then_respond_sends_final_answer():
    notifier = _FakeNotifier()
    c = _container(
        [
            {"action": "call_tool", "tool": "list_applications", "tool_args": {"limit": "5"}},
            {"action": "respond", "response": "지원 건이 1개 있어요."},
        ],
        notifier=notifier,
    )

    await handle_chat("오늘 지원 몇 건이야?", c, _FakeClient())

    assert [e.message for e in notifier.notified] == ["지원 건이 1개 있어요."]


async def test_resend_pending_decision_calls_notifier_without_signaling_workflow():
    """행동성 도구도 워크플로우를 직접 mutate 하지 않는다 — nonce 를 그대로 실어 재전송만 한다."""
    notifier = _FakeNotifier()
    c = _container(
        [
            {
                "action": "call_tool",
                "tool": "resend_pending_decision",
                "tool_args": {"application_id": "app_1"},
            },
            {"action": "respond", "response": "버튼을 다시 보냈어요."},
        ],
        notifier=notifier,
    )
    client = _FakeClient(_FakeHandle(view=PendingDecisionView(has_pending=True, nonce="nonce_1")))

    await handle_chat("app_1 승인 버튼 다시 보내줘", c, client)

    assert notifier.resent == [("app_1", "nonce_1")]
    assert [e.message for e in notifier.notified] == ["버튼을 다시 보냈어요."]


async def test_resend_pending_decision_reports_when_nothing_pending():
    notifier = _FakeNotifier()
    c = _container(
        [
            {
                "action": "call_tool",
                "tool": "resend_pending_decision",
                "tool_args": {"application_id": "app_1"},
            },
            {"action": "respond", "response": "확인했어요."},
        ],
        notifier=notifier,
    )
    client = _FakeClient(_FakeHandle(view=PendingDecisionView(has_pending=False)))

    await handle_chat("app_1 승인 버튼 다시 보내줘", c, client)

    assert notifier.resent == []  # 대기 중인 승인이 없으면 재전송을 시도조차 하지 않는다


async def test_unknown_tool_name_is_self_corrected_not_crashed():
    notifier = _FakeNotifier()
    c = _container(
        [
            {"action": "call_tool", "tool": "no_such_tool", "tool_args": {}},
            {"action": "respond", "response": "죄송해요, 그 요청은 처리할 수 없어요."},
        ],
        notifier=notifier,
    )

    await handle_chat("이상한 요청", c, _FakeClient())

    assert [e.message for e in notifier.notified] == ["죄송해요, 그 요청은 처리할 수 없어요."]


async def test_step_budget_exhausted_sends_apology_instead_of_looping_forever():
    notifier = _FakeNotifier()
    payloads = [
        {"action": "call_tool", "tool": "list_applications", "tool_args": {}}
        for _ in range(MAX_STEPS)
    ]
    c = _container(payloads, notifier=notifier)

    await handle_chat("계속 도구만 부르는 상황", c, _FakeClient())

    assert len(notifier.notified) == 1
    assert "죄송해요" in notifier.notified[0].message


async def test_llm_schema_violation_does_not_raise_and_apologizes():
    """StubLLM 이 스키마에 안 맞는(빈) payload 를 내도(quota/모델 오류 흉내) 대화가 안 죽는다."""
    notifier = _FakeNotifier()
    c = _container([], notifier=notifier)  # 빈 payload → AgentStep 필수 필드 누락 → 스키마 위반

    await handle_chat("아무 말", c, _FakeClient())

    assert len(notifier.notified) == 1
    assert "이해하지 못했어요" in notifier.notified[0].message


def _actionable_job_rows() -> dict:
    job = JobPosting(
        platform="wanted", platform_job_id="1", url="https://x/1", company="A사", title="백엔드"
    )
    record = JobRecord(
        job=job,
        screening=ScreeningVerdict(verdict="pass", fit_score=80),
        applicability=ApplicabilityVerdict(
            actionable=True, channel="platform_form", apply_url=job.url
        ),
        collected_at=SystemClock().now(),
    )
    return {(job.platform, job.platform_job_id): record}


async def test_start_applications_tool_starts_workflow_for_actionable_job():
    notifier = _FakeNotifier()
    harness = Harness(job_rows=_actionable_job_rows())
    c = _container(
        [
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "3"}},
            {"action": "respond", "response": "1건 지원 시작했어요."},
        ],
        notifier=notifier,
        harness=harness,
    )
    client = _FakeClient()

    await handle_chat("3건 제출해줘", c, client)

    assert len(client.started) == 1
    assert [e.message for e in notifier.notified] == ["1건 지원 시작했어요."]


async def test_start_applications_tool_reports_when_nothing_actionable():
    notifier = _FakeNotifier()
    c = _container(
        [
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "3"}},
            {"action": "respond", "response": "지원 가능한 공고가 없어요."},
        ],
        notifier=notifier,
    )
    client = _FakeClient()

    await handle_chat("지원해줘", c, client)

    assert client.started == []
    assert [e.message for e in notifier.notified] == ["지원 가능한 공고가 없어요."]


async def test_start_applications_dry_run_previews_without_starting_workflow():
    notifier = _FakeNotifier()
    harness = Harness(job_rows=_actionable_job_rows())
    c = _container(
        [
            {
                "action": "call_tool",
                "tool": "start_applications",
                "tool_args": {"count": "3", "dry_run": "true"},
            },
            {"action": "respond", "response": "미리보기 결과예요."},
        ],
        notifier=notifier,
        harness=harness,
    )
    client = _FakeClient()

    await handle_chat("일단 테스트로 뭐가 뽑히는지만 보여줘", c, client)

    assert client.started == []  # dry_run 이면 실제로 워크플로우를 시작하지 않는다
    assert [e.message for e in notifier.notified] == ["미리보기 결과예요."]
