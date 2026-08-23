"""collect_now 도구 (telegram/_agent_tools_collect.py).

`_FakeClient.start_workflow`가 `JobCollectionWorkflow`를 실제로 시작하는 대신 미리 준비된
`CollectJobsResult`를 돌려주는 handle을 리턴한다 — 실제 Temporal/Playwright 없이 도구의
포맷팅·배선만 검증한다. `JobCollectionWorkflow` 자체의 동작은 `tests/workflows/`가 이미
검증한다.
"""

import dataclasses

from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import ScheduleConfig
from auto_apply.contracts.job import CollectJobsResult, PlatformCollectionResult
from auto_apply.telegram._agent_tools_collect import _collect_now
from auto_apply.telegram.agent import handle_chat
from tests.conftest import Harness
from tests.telegram.test_agent import _FakeNotifier


class _FakeHandle:
    def __init__(self, result: CollectJobsResult) -> None:
        self._result = result

    async def result(self) -> CollectJobsResult:
        return self._result


class _FakeClient:
    def __init__(self, result: CollectJobsResult) -> None:
        self._result = result
        self.started: list[dict[str, object]] = []

    async def start_workflow(self, fn, cmd, *, id, task_queue):
        self.started.append({"id": id, "task_queue": task_queue, "cmd": cmd})
        return _FakeHandle(self._result)


def _container() -> Container:
    return Harness().container(settings=Settings(storage="memory", llm_provider="stub"))


async def test_collect_now_uses_configured_platforms_and_reports_result():
    c = _container()
    async with c.uow() as uow:
        await uow.schedule_config.set(
            ScheduleConfig(target="collection", hour=9, minute=0, platforms=["wanted", "saramin"])
        )
        await uow.commit()
    result = CollectJobsResult(
        results=[
            PlatformCollectionResult(platform="wanted", found=10, passed=4, actionable=3),
            PlatformCollectionResult(
                platform="saramin", found=5, passed=1, actionable=0, error="boom"
            ),
        ]
    )
    client = _FakeClient(result)

    message = await _collect_now({}, c, client)

    assert len(client.started) == 1
    assert client.started[0]["cmd"].platforms == ["wanted", "saramin"]
    assert "wanted: found=10 passed=4 actionable=3" in message
    assert "saramin: found=5 passed=1 actionable=0 error=boom" in message


async def test_collect_now_seeds_from_settings_when_no_db_config():
    c = Harness().container(
        settings=Settings(storage="memory", llm_provider="stub", job_collection_platforms="wanted")
    )
    client = _FakeClient(CollectJobsResult(results=[]))

    await _collect_now({}, c, client)

    assert client.started[0]["cmd"].platforms == ["wanted"]


async def test_collect_now_tool_is_wired_into_chat_agent():
    notifier = _FakeNotifier()
    stub = StubLLM(
        payloads=[
            {"action": "call_tool", "tool": "collect_now", "tool_args": {}},
            {"action": "respond", "response": "공고 수집을 실행했어요."},
        ]
    )
    c = dataclasses.replace(_container(), llm=stub, chat_llm=stub, notifier=notifier)
    client = _FakeClient(CollectJobsResult(results=[]))

    await handle_chat("지금 공고 수집해줘", c, client)

    assert [e.message for e in notifier.notified] == ["공고 수집을 실행했어요."]
    assert len(client.started) == 1
