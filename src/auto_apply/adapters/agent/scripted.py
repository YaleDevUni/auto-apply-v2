"""AgentRuntime 테스트 대역 — 미리 적은 도구 호출을 차례로 재생한다 (§A6).

한 걸음은 `ScriptedCall` 이거나, 지금까지 받은 답을 보고 다음 호출을 정하는 함수다(snapshot 의
ref 를 골라야 하는 짐 테스트용). 함수가 None 을 돌려주면 에이전트가 거기서 스스로 멈춘 것이다.
LLM 이 없어 토큰은 0 이다.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentOutcome, AgentTool, ToolReply
from auto_apply.ports.agent import CallTool


@dataclass(frozen=True, slots=True)
class ScriptedCall:
    name: str
    args: Mapping[str, object] = field(default_factory=dict)


ScriptStep = ScriptedCall | Callable[[Sequence[ToolReply]], ScriptedCall | None]


class ScriptedAgentRuntime:
    def __init__(self, script: Sequence[ScriptStep] = (), *, text: str = "") -> None:
        self._script = tuple(script)
        self._text = text
        self.replies: list[ToolReply] = []  # 받은 답 — 테스트가 무엇을 돌려받았는지 본다
        self.seen_prompt: str | None = None
        self.seen_tools: tuple[AgentTool, ...] = ()

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        self.seen_prompt, self.seen_tools = system_prompt, tuple(tools)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.max_seconds
        calls = 0
        for step in self._script:
            if loop.time() >= deadline:
                return self._outcome(AgentEnd.TIME_LIMIT, calls)
            call = step(tuple(self.replies)) if callable(step) else step
            if call is None:
                break
            if calls >= limits.max_tool_calls:
                return self._outcome(AgentEnd.TOOL_LIMIT, calls)
            calls += 1
            reply = await call_tool(call.name, call.args)
            self.replies.append(reply)
            if reply.done:
                break
        return self._outcome(AgentEnd.COMPLETED, calls)

    def _outcome(self, ended: AgentEnd, calls: int) -> AgentOutcome:
        return AgentOutcome(ended=ended, tool_calls=calls, text=self._text)
