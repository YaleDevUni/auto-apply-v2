"""에이전트 런타임 port (§A6). 구현: Scripted(대역) · ClaudeCli · AnthropicApi(T3.8).

런타임은 브라우저를 직접 만지지 않는다 — 도구 실행은 호출자가 넘긴 `call_tool` 로만 한다.
tests/ports/test_agent_runtime_contract.py 가 모든 구현에 같은 기대를 건다.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol

from auto_apply.contracts.agent import AgentLimits, AgentOutcome, AgentTool, ToolReply

CallTool = Callable[[str, Mapping[str, object]], Awaitable[ToolReply]]


class AgentRuntime(Protocol):
    """계약:
    - 도구는 `call_tool(name, args)` 로만 부른다. 없는 이름·잘못된 인자도 그대로 넘긴다 —
      거부는 `call_tool` 이 `ToolReply(ok=False)` 로 돌려주고, 런타임은 멈추지 않고 이어간다.
    - `ToolReply.done` 을 받으면 더 부르지 않고 `COMPLETED` 로 돌아온다.
    - `limits.max_tool_calls` 번을 넘겨 부르지 않는다 — 더 부르려 했으면 `TOOL_LIMIT`.
      `limits.max_seconds` 를 넘기면 `TIME_LIMIT` (호출자도 따로 끊는다).
    - `run_id` 는 기록(transcript 등)을 둘 이름이다 — 없으면 구현이 정한다. 도구 통로와는 상관없다.
    - 모델·프로세스 자체의 실패는 예외(`LLMExecutionError` 계열)로 알린다.
    """

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome: ...
