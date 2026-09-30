"""run 토큰 — MCP(HTTP)로 run 하나의 도구 통로를 여는 일회성 비밀 (§A5, §A10).

CLI 런타임(T3.6)은 `claude` 프로세스가 도구를 MCP 로 부른다. 그 요청이 어느 run 의 `call_tool` 로
가야 하는지를 토큰이 정한다 — 런타임이 `run()` 안에서 `open(call_tool)` 으로 받고, 블록을 나가면
(끝·예외·취소) 폐기된다. 설치 토큰(`session_token`)과는 별개라 서로의 통로를 열지 못한다.
토큰은 프로세스 메모리에만 있다: 앱이 다시 뜨면 진행 중이던 run 도 크래시 복구로 끝난다(§A9).
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from auto_apply.ports.agent import CallTool


class RunTokens:
    def __init__(self) -> None:
        self._live: dict[str, CallTool] = {}

    @asynccontextmanager
    async def open(self, call_tool: CallTool) -> AsyncIterator[str]:
        """이 블록 동안만 유효한 토큰. MCP 로 온 도구 호출은 `call_tool` 로 간다."""
        token = secrets.token_urlsafe(32)
        self._live[token] = call_tool
        try:
            yield token
        finally:
            del self._live[token]

    def resolve(self, token: str | None) -> CallTool | None:
        """살아 있는 토큰이면 그 run 의 통로. 상수 시간 비교 — 한 글자씩 맞혀 보지 못하게."""
        if not token:
            return None
        given = token.encode()
        for live, call_tool in list(self._live.items()):
            if secrets.compare_digest(live.encode(), given):
                return call_tool
        return None

    @property
    def live_count(self) -> int:
        return len(self._live)
