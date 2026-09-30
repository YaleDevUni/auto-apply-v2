"""가짜 Anthropic Messages API — httpx 전송 대역. 실제 API 는 부르지 않는다 (T3.8).

`FakeMessagesApi(turns)`: 요청마다 `turns` 를 하나씩 꺼내 응답한다. 한 턴은 도구 호출 목록
(→ `stop_reason=tool_use`)이거나 문자열(→ `end_turn` 텍스트)이다. 턴이 떨어지면 빈 `end_turn`.
턴 자리에 함수를 두면 지금까지 받은 tool_result 내용(문자열 목록)을 보고 턴을 정한다
(snapshot ref 고르기).
`fail(status, type, message)` 로 에러 응답을 낸다. 받은 요청 본문은 `requests` 에 남는다.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from auto_apply.adapters.agent.anthropic_api import AnthropicApiAgentRuntime

Calls = Sequence[tuple[str, Mapping[str, object]]]
Turn = Calls | str | Callable[[list[str]], "Calls | str"]
KEY = "sk-ant-test-0123456789"


class ClosingTransport(httpx.MockTransport):
    """닫혔는지 본다 — run 이 끝·예외·취소 어느 쪽이든 연결을 닫아야 한다."""

    closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class FakeMessagesApi:
    def __init__(self, turns: Sequence[Turn] = ()) -> None:
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self._error: tuple[int, dict[str, Any]] | None = None
        self._ids = 0
        self.stop_reason: str | None = None  # 강제로 바꿀 stop_reason (max_tokens 등)
        self.transport = ClosingTransport(self._handle)

    def fail(self, status: int, error_type: str, message: str = "") -> "FakeMessagesApi":
        self._error = (status, {"type": "error", "error": {"type": error_type, "message": message}})
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        if self._error:
            return httpx.Response(self._error[0], json=self._error[1])
        turn = self._turns.pop(0) if self._turns else ""
        if callable(turn):
            turn = turn(_tool_results(self.requests[-1]))
        return httpx.Response(200, json=self._message(turn))

    def _message(self, turn: Calls | str) -> dict[str, Any]:
        if isinstance(turn, str):
            content: list[dict[str, Any]] = [{"type": "text", "text": turn}] if turn else []
            stop = "end_turn"
        else:
            content = [{"type": "text", "text": "도구를 부른다"}]
            for name, args in turn:
                self._ids += 1
                content.append(
                    {"type": "tool_use", "id": f"toolu_{self._ids:03d}", "name": name,
                     "input": dict(args)}
                )  # fmt: skip
            stop = "tool_use"
        stop = self.stop_reason or stop
        return {
            "id": f"msg_{len(self.requests):03d}", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5-5", "content": content, "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 3},
        }  # fmt: skip


def _tool_results(body: dict[str, Any]) -> list[str]:
    return [
        b["content"]
        for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"] if b.get("type") == "tool_result"
    ]  # fmt: skip


def one_call_per_turn(intent: Sequence[tuple[str, Mapping[str, object]]]) -> list[Turn]:
    return [[(name, args)] for name, args in intent]


def api_runtime(
    api: FakeMessagesApi, runs_dir: Path, *, key: str = KEY
) -> AnthropicApiAgentRuntime:
    return AnthropicApiAgentRuntime(
        key, model="claude-sonnet-5-5", runs_dir=runs_dir, max_retries=0, transport=api.transport
    )
