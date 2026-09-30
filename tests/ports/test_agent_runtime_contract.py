"""AgentRuntime contract test (ports/agent.py) — 모든 구현에 같은 기대를 건다.

구현마다 "에이전트가 이 순서로 도구를 부르고 싶어 한다"를 만드는 방법이 다르다: Scripted 는
스크립트 그대로, CLI·API 구현(T3.6~)은 그 순서를 지시한 프롬프트·가짜 모델로 붙인다(MAKERS 에 추가).
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence

import pytest

from auto_apply.adapters.agent.scripted import ScriptedAgentRuntime, ScriptedCall
from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentTool, ToolReply
from auto_apply.ports.agent import AgentRuntime

Intent = Sequence[tuple[str, Mapping[str, object]]]


def _scripted(intent: Intent) -> AgentRuntime:
    return ScriptedAgentRuntime([ScriptedCall(name, args) for name, args in intent])


MAKERS: dict[str, Callable[[Intent], AgentRuntime]] = {"scripted": _scripted}
TOOLS = (AgentTool(name="echo", description="되돌려 준다", input_schema={"type": "object"}),)


@pytest.fixture(params=sorted(MAKERS))
def make(request: pytest.FixtureRequest) -> Callable[[Intent], AgentRuntime]:
    return MAKERS[request.param]


class Tools:
    """호출자 쪽 `call_tool` — 이름이 echo 가 아니면 거부, `done_on` 번째 호출에 done."""

    def __init__(self, *, done_on: int | None = None, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._done_on, self._delay = done_on, delay_s

    async def __call__(self, name: str, args: Mapping[str, object]) -> ToolReply:
        self.calls.append((name, dict(args)))
        if self._delay:
            await asyncio.sleep(self._delay)
        done = self._done_on is not None and len(self.calls) >= self._done_on
        ok = name == "echo" and set(args) <= {"x"}
        return ToolReply(ok=ok, content=f'{{"ok": {str(ok).lower()}}}', done=done)


async def test_calls_go_through_call_tool_in_order(make):
    tools = Tools()
    intent = [("echo", {"x": 1}), ("echo", {"x": 2})]
    out = await make(intent).run("sys", TOOLS, tools, limits=AgentLimits())
    assert tools.calls == [("echo", {"x": 1}), ("echo", {"x": 2})]
    assert out.ended is AgentEnd.COMPLETED and out.tool_calls == 2


async def test_rejected_calls_do_not_stop_the_run(make):
    tools = Tools()
    intent = [("no_such_tool", {}), ("echo", {"bad": True}), ("echo", {"x": 3})]
    out = await make(intent).run("sys", TOOLS, tools, limits=AgentLimits())
    assert [c[0] for c in tools.calls] == ["no_such_tool", "echo", "echo"]
    assert out.ended is AgentEnd.COMPLETED and out.tool_calls == 3


async def test_stops_after_done(make):
    tools = Tools(done_on=2)
    intent = [("echo", {"x": i}) for i in range(5)]
    out = await make(intent).run("sys", TOOLS, tools, limits=AgentLimits())
    assert len(tools.calls) == 2
    assert out.ended is AgentEnd.COMPLETED


async def test_never_exceeds_tool_limit(make):
    tools = Tools()
    intent = [("echo", {"x": i}) for i in range(10)]
    out = await make(intent).run("sys", TOOLS, tools, limits=AgentLimits(max_tool_calls=3))
    assert len(tools.calls) == 3
    assert out.ended is AgentEnd.TOOL_LIMIT


async def test_time_limit(make):
    tools = Tools(delay_s=0.05)
    intent = [("echo", {"x": i}) for i in range(50)]
    out = await make(intent).run("sys", TOOLS, tools, limits=AgentLimits(max_seconds=0.12))
    assert out.ended is AgentEnd.TIME_LIMIT
    assert len(tools.calls) < 50


async def test_scripted_step_can_read_previous_replies():
    seen: list[int] = []

    def step(replies: Sequence[ToolReply]) -> ScriptedCall | None:
        seen.append(len(replies))
        return None  # 에이전트가 스스로 멈춘다

    runtime = ScriptedAgentRuntime([ScriptedCall("echo", {"x": 1}), step, ScriptedCall("echo")])
    tools = Tools()
    out = await runtime.run("시스템", TOOLS, tools, limits=AgentLimits())
    assert seen == [1] and len(tools.calls) == 1
    assert out.ended is AgentEnd.COMPLETED
    assert runtime.seen_prompt == "시스템" and runtime.seen_tools == TOOLS
