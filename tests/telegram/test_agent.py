"""telegram/agent.py — ReAct 루프 + 도구 레지스트리.

`StubLLM(payloads=[...])`로 매 턴의 `AgentStep`을 스크립트하고, 실제 Temporal/네트워크 없이
도구가 올바른 관찰 결과를 만들고 최종 답이 notifier 로 나가는지만 본다.
"""

import dataclasses

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.config import Settings
from auto_apply.contracts.dto import NotifyEvent, PendingDecisionView, PersistState
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.chat_agent import MAX_BLOCKED_STEPS, MAX_STEPS
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


async def test_step_budget_exhausted_reports_tools_that_already_ran():
    """스텝을 다 써도 실행된 도구가 있으면 사과 대신 그 결과를 보낸다.

    실측(2026-08-22): "스케줄 시각 바꾸고 켜줘"가 스케줄을 실제로 바꿔놓고도 "다 처리하지
    못했어요"만 보내서, 사용자가 기능이 실패한 줄 알고 같은 요청을 반복했다.
    """
    notifier = _FakeNotifier()
    # 같은 도구를 같은 인자로 계속 부른다 → 첫 1회만 실행되고 나머지는 blocked 예산을 태운다
    payloads = [
        {"action": "call_tool", "tool": "list_applications", "tool_args": {}}
        for _ in range(MAX_STEPS + MAX_BLOCKED_STEPS + 2)
    ]
    c = _container(payloads, notifier=notifier)

    await handle_chat("계속 도구만 부르는 상황", c, _FakeClient())

    assert len(notifier.notified) == 1
    assert "list_applications" in notifier.notified[0].message


async def test_blocked_duplicate_calls_do_not_eat_the_progress_budget():
    """중복 호출은 아무 일도 안 하므로 MAX_STEPS 를 소모하면 안 된다.

    실측(2026-08-22): 모델이 성공한 도구를 한 번씩 더 부르는 습성 때문에 도구 2개짜리
    요청에 중복 시도가 4번 붙었다. 같이 세면 도구 3개짜리 요청이 다시 소진된다.
    """
    notifier = _FakeNotifier()
    # 매 도구 호출마다 같은 호출을 한 번씩 중복해서 끼워넣는다 — 두 예산을 각각 거의
    # 끝까지 쓰되 넘기지는 않는 개수로.
    pairs = min(MAX_STEPS, MAX_BLOCKED_STEPS) - 1
    assert 2 * pairs >= MAX_STEPS, "예산을 하나로 세던 시절엔 소진됐을 길이여야 의미가 있다"
    payloads: list[dict] = []
    for i in range(pairs):
        call = {"action": "call_tool", "tool": "list_applications", "tool_args": {"limit": str(i)}}
        payloads += [call, dict(call)]  # 실행 1회 + 중복 1회
    payloads.append({"action": "respond", "response": "다 했어요"})
    c = _container(payloads, notifier=notifier)

    await handle_chat("도구를 여러 번 부르는 상황", c, _FakeClient())

    # 중복이 MAX_STEPS 만큼 끼어도 진전 예산은 그대로라 끝까지 가서 respond 로 끝난다
    assert [e.message for e in notifier.notified] == ["다 했어요"]


async def test_repeating_the_same_call_forever_still_terminates():
    """중복 예산(MAX_BLOCKED_STEPS)이 무한 루프를 막는다 — respond 가 영원히 안 나와도 끝난다."""
    notifier = _FakeNotifier()
    payloads = [
        {"action": "call_tool", "tool": "list_applications", "tool_args": {}} for _ in range(200)
    ]
    c = _container(payloads, notifier=notifier)

    await handle_chat("영원히 같은 도구만 부르는 상황", c, _FakeClient())

    assert len(notifier.notified) == 1


async def test_step_budget_exhausted_apologizes_when_no_tool_ever_ran():
    notifier = _FakeNotifier()
    payloads = [
        {"action": "call_tool", "tool": "no_such_tool", "tool_args": {"i": str(i)}}
        for i in range(MAX_STEPS + MAX_BLOCKED_STEPS + 2)
    ]
    c = _container(payloads, notifier=notifier)

    await handle_chat("존재하지 않는 도구만 부르는 상황", c, _FakeClient())

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


async def test_start_applications_is_single_shot_per_turn():
    """실측(2026-08-21): 모델이 성공 관찰 결과를 보고도 respond 안 하고 같은 도구를 또 불러서

    "2건정도"라는 요청이 실제로는 여러 번 실행돼버린 적이 있다 — 두 번째 호출은 코드가
    막고(관찰 결과만 돌려주고 실제 실행은 스킵) 실행 횟수가 1번을 넘지 않아야 한다.
    """
    notifier = _FakeNotifier()
    harness = Harness(job_rows=_actionable_job_rows())
    c = _container(
        [
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "2"}},
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "2"}},
            {"action": "respond", "response": "다 처리했어요."},
        ],
        notifier=notifier,
        harness=harness,
    )
    client = _FakeClient()

    await handle_chat("지원시작 2건정도", c, client)

    assert len(client.started) == 1  # 두 번째 call_tool 은 실제로 실행되지 않았다
    assert [e.message for e in notifier.notified] == ["다 처리했어요."]


async def test_identical_tool_call_is_deduped_within_a_turn():
    """SINGLE_SHOT_TOOLS 가 아니어도, 완전히 같은 (도구, 인자) 반복 호출은 실제로 재실행하지

    않는다 — resend_pending_decision 같은 부작용 있는 도구를 실수로 두 번 부르는 것도 막는다.
    """
    notifier = _FakeNotifier()
    c = _container(
        [
            {
                "action": "call_tool",
                "tool": "resend_pending_decision",
                "tool_args": {"application_id": "app_1"},
            },
            {
                "action": "call_tool",
                "tool": "resend_pending_decision",
                "tool_args": {"application_id": "app_1"},
            },
            {"action": "respond", "response": "다시 보냈어요."},
        ],
        notifier=notifier,
    )
    client = _FakeClient(_FakeHandle(view=PendingDecisionView(has_pending=True, nonce="nonce_1")))

    await handle_chat("app_1 버튼 다시 보내줘", c, client)

    assert notifier.resent == [("app_1", "nonce_1")]  # 한 번만 실제로 재전송됐다


async def test_start_applications_different_args_are_still_single_shot():
    """SINGLE_SHOT_TOOLS 는 인자가 달라도(count 를 바꿔서 재시도해도) 한 턴 1회로 막는다 —

    (도구, 인자) 완전 일치 dedup 만으로는 count 를 슬쩍 바꾼 재호출을 못 잡는다.
    """
    notifier = _FakeNotifier()
    harness = Harness(job_rows=_actionable_job_rows())
    c = _container(
        [
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "2"}},
            {"action": "call_tool", "tool": "start_applications", "tool_args": {"count": "5"}},
            {"action": "respond", "response": "다 처리했어요."},
        ],
        notifier=notifier,
        harness=harness,
    )
    client = _FakeClient()

    await handle_chat("지원시작 2건정도", c, client)

    assert len(client.started) == 1


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


_WANTED_URL = "https://www.wanted.co.kr/wd/12345"


def _wanted_harness() -> Harness:
    registry = StaticPlatformRegistry(
        [FixturePlatformAdapter(platform="wanted", hosts=("www.wanted.co.kr",))]
    )
    return Harness(registry=registry)


async def test_apply_by_url_tool_starts_workflow_for_wanted_link():
    notifier = _FakeNotifier()
    c = _container(
        [
            {"action": "call_tool", "tool": "apply_by_url", "tool_args": {"url": _WANTED_URL}},
            {"action": "respond", "response": "지원 시작했어요."},
        ],
        notifier=notifier,
        harness=_wanted_harness(),
    )
    client = _FakeClient()

    await handle_chat(f"이 링크 지원해줘 {_WANTED_URL}", c, client)

    assert len(client.started) == 1
    assert [e.message for e in notifier.notified] == ["지원 시작했어요."]


async def test_apply_by_url_tool_rejects_non_wanted_link():
    notifier = _FakeNotifier()
    c = _container(
        [
            {
                "action": "call_tool",
                "tool": "apply_by_url",
                "tool_args": {"url": "https://example.com/job/1"},
            },
            {"action": "respond", "response": "원티드 링크만 지원해요."},
        ],
        notifier=notifier,
        # 기본 harness = FixturePlatformAdapter(platform="fixture") 뿐이라 example.com 은
        # registry.for_url 부터 걸린다.
    )
    client = _FakeClient()

    await handle_chat("이거 지원해줘 https://example.com/job/1", c, client)

    assert client.started == []
    assert [e.message for e in notifier.notified] == ["원티드 링크만 지원해요."]
