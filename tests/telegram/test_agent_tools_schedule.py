"""schedule_status/set_schedule_enabled 도구 (telegram/_agent_tools_schedule.py, § apply-schedule).

Temporal Schedule RPC(create_schedule/get_schedule_handle().describe/pause/unpause) 를 최소로
흉내내는 `_FakeClient`로 도구 함수를 직접 부른다 — `schedule.py`의 `ensure_*_schedule`이 실제로
쓰는 RPC 왕복(create_schedule → ScheduleAlreadyRunningError → update)은
`tests/test_schedule.py`의 왕복 테스트가 실제 Temporal 로
이미 검증한다. `TOOLS` 레지스트리에 잘 병합됐는지는 `handle_chat` 을 한 번 거치는 라우팅
테스트 하나로 확인한다(`tests/telegram/test_agent.py`와 같은 패턴).
"""

import dataclasses
from types import SimpleNamespace

from temporalio.client import ScheduleAlreadyRunningError
from temporalio.service import RPCError, RPCStatusCode

from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.schedule import APPLY_INTAKE_SCHEDULE_ID, JOB_COLLECTION_SCHEDULE_ID
from auto_apply.telegram._agent_tools_schedule import (
    _schedule_status,
    _set_schedule_enabled,
    _set_schedule_time,
)
from auto_apply.telegram.agent import handle_chat
from tests.conftest import Harness
from tests.telegram.test_agent import _FakeNotifier


def _not_found() -> RPCError:
    return RPCError("schedule not found", RPCStatusCode.NOT_FOUND, b"")


class _FakeScheduleHandle:
    def __init__(self, *, exists: bool) -> None:
        self.exists = exists
        self.paused = False
        self.schedule: object | None = None  # 마지막으로 create/update 된 실제 Schedule 객체
        self.pause_notes: list[str | None] = []
        self.unpause_notes: list[str | None] = []

    async def describe(self):
        if not self.exists:
            raise _not_found()
        return SimpleNamespace(
            schedule=SimpleNamespace(state=SimpleNamespace(paused=self.paused)),
            info=SimpleNamespace(next_action_times=[]),
        )

    async def pause(self, *, note: str | None = None) -> None:
        if not self.exists:
            raise _not_found()
        self.paused = True
        self.pause_notes.append(note)

    async def unpause(self, *, note: str | None = None) -> None:
        self.paused = False
        self.unpause_notes.append(note)

    async def update(self, updater) -> None:
        # 실제 Temporal 처럼: update 는 새 Schedule 객체를 통째로 심고, 그 객체의 기본
        # state.paused 는 False 다 — schedule.py._create_or_update 가 이 뒤에 명시적으로
        # 다시 pause() 하지 않으면 꺼둔 스케줄이 조용히 켜진다(실측, § apply-schedule).
        self.schedule = updater(None).schedule
        self.paused = False


class _FakeClient:
    """`get_schedule_handle`은 항상 같은 인스턴스를 돌려준다(진짜 Temporal 처럼) — 없으면

    `exists=False`인 핸들을 새로 만들어 캐싱한다. `create_schedule`은 real client 와 같은
    계약(schedule.py 가 그 계약에 기대 짜여 있다) — 이미 있으면 `ScheduleAlreadyRunningError`.
    """

    def __init__(self) -> None:
        self._handles: dict[str, _FakeScheduleHandle] = {}

    def get_schedule_handle(self, schedule_id: str) -> _FakeScheduleHandle:
        return self._handles.setdefault(schedule_id, _FakeScheduleHandle(exists=False))

    async def create_schedule(self, schedule_id: str, schedule) -> None:
        handle = self.get_schedule_handle(schedule_id)
        if handle.exists:
            raise ScheduleAlreadyRunningError
        handle.exists = True
        handle.schedule = schedule


def _container() -> Container:
    return Harness().container(settings=Settings(storage="memory", llm_provider="stub"))


async def test_status_reports_registered_and_unregistered_schedules():
    client = _FakeClient()
    client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID).exists = True
    # APPLY_INTAKE_SCHEDULE_ID 는 등록 안 된 채로 둔다.

    result = await _schedule_status({}, _container(), client)

    assert "공고 수집: 켜짐" in result
    assert "자동 지원: 등록 안 됨" in result


async def test_disable_pauses_existing_schedule():
    client = _FakeClient()
    client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID).exists = True

    result = await _set_schedule_enabled(
        {"target": "apply", "enabled": "false"}, _container(), client
    )

    assert "껐습니다" in result
    handle = client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID)
    assert handle.paused is True


async def test_disable_unregistered_schedule_reports_already_off():
    client = _FakeClient()

    result = await _set_schedule_enabled(
        {"target": "collection", "enabled": "false"}, _container(), client
    )

    assert "이미 꺼진" in result


async def test_enable_creates_missing_schedule_and_unpauses():
    client = _FakeClient()

    result = await _set_schedule_enabled(
        {"target": "collection", "enabled": "true"}, _container(), client
    )

    assert "켰습니다" in result
    handle = client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID)
    assert handle.exists is True
    assert handle.paused is False


async def test_enable_unknown_target_is_rejected():
    result = await _set_schedule_enabled(
        {"target": "nonsense", "enabled": "true"}, _container(), _FakeClient()
    )

    assert "collection" in result and "apply" in result


async def test_set_schedule_time_persists_to_db_and_pushes_to_temporal():
    c = _container()

    result = await _set_schedule_time(
        {"target": "apply", "hour": "14", "minute": "30", "count": "5"}, c, _FakeClient()
    )

    assert "14:30" in result
    async with c.uow() as uow:
        saved = await uow.schedule_config.get("apply")
    assert saved is not None
    assert (saved.hour, saved.minute, saved.count) == (14, 30, 5)


async def test_set_schedule_time_keeps_existing_count_when_omitted():
    c = _container()
    client = _FakeClient()
    await _set_schedule_time({"target": "apply", "hour": "9", "count": "7"}, c, client)

    await _set_schedule_time({"target": "apply", "hour": "11"}, c, client)

    async with c.uow() as uow:
        saved = await uow.schedule_config.get("apply")
    assert saved is not None
    assert (saved.hour, saved.count) == (11, 7)  # count 는 그대로 유지


async def test_set_schedule_time_does_not_silently_reenable_a_paused_schedule():
    c = _container()
    client = _FakeClient()
    await _set_schedule_enabled({"target": "apply", "enabled": "true"}, c, client)
    await _set_schedule_enabled({"target": "apply", "enabled": "false"}, c, client)
    handle = client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID)
    assert handle.paused is True

    await _set_schedule_time({"target": "apply", "hour": "15"}, c, client)

    assert handle.paused is True  # 시각만 바뀌었을 뿐 꺼진 상태는 유지된다


async def test_set_schedule_time_rejects_out_of_range_hour():
    result = await _set_schedule_time(
        {"target": "apply", "hour": "24", "minute": "0"}, _container(), _FakeClient()
    )

    assert "0~23" in result


async def test_set_schedule_time_rejects_non_numeric_hour():
    result = await _set_schedule_time(
        {"target": "apply", "hour": "오후", "minute": "0"}, _container(), _FakeClient()
    )

    assert "숫자" in result


async def test_schedule_status_tool_is_wired_into_chat_agent():
    notifier = _FakeNotifier()
    stub = StubLLM(
        payloads=[
            {"action": "call_tool", "tool": "schedule_status", "tool_args": {}},
            {"action": "respond", "response": "스케줄 상태를 확인했어요."},
        ]
    )
    c = dataclasses.replace(_container(), llm=stub, chat_llm=stub, notifier=notifier)

    await handle_chat("스케줄 상태 알려줘", c, _FakeClient())

    assert [e.message for e in notifier.notified] == ["스케줄 상태를 확인했어요."]
