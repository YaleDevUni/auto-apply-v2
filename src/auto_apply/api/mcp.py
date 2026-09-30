"""BrowserToolbox 의 MCP(streamable HTTP) 노출 — CLI 런타임의 도구 통로 (§A5, §A10).

도구 목록은 `browser_toolbox_specs` 그대로다(여기서 도구를 정의하지 않는다). 호출은 요청에 붙은 run
토큰(`Authorization: Bearer`)이 가리키는 그 run 의 `call_tool`(= FillSession → BrowserToolbox.call)
로만 간다 — 한도 계수·FillLog 가 in-process 경로와 같다. Host·Origin 은 앱 전체의 보안 미들웨어가
먼저 보고, 설치 토큰 검사 대신 여기서 run 토큰을 본다. 상태 없는(stateless) JSON 응답 모드다:
요청마다 토큰을 다시 보므로 run 이 끝난 뒤의 요청은 세션이 남아 있어도 열리지 않는다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers
from starlette.types import Receive, Scope, Send

from auto_apply import __version__
from auto_apply.api.errors import error_response
from auto_apply.services.browser_toolbox_specs import agent_tools
from auto_apply.services.run_tokens import RunTokens

log = structlog.get_logger(__name__)

MCP_PATH = "/mcp"
_SCOPE_TOKEN = "auto_apply.run_token"


def bearer_token(headers: Headers) -> str | None:
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _run_tokens(scope: Scope) -> RunTokens:
    tokens: RunTokens = scope["app"].state.container.run_tokens
    return tokens


def _build_server() -> Server[Any, Any]:
    server: Server[Any, Any] = Server("auto-apply", version=__version__)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
            for t in agent_tools()
        ]

    # 입력 검증은 도구 상자가 한다(§A6 — 잘못된 인자도 ToolResult 에러로 돌려받고 run 은 계속)
    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        request = server.request_context.request
        scope: Scope = request.scope  # type: ignore[union-attr]
        # 요청이 들어온 뒤 run 이 끝났을 수 있다 — 도구에 닿기 직전에 한 번 더 본다
        call = _run_tokens(scope).resolve(scope.get(_SCOPE_TOKEN))
        if call is None:
            return _error("run 이 끝났다 — 더 부르지 않는다")
        try:
            reply = await call(name, arguments)
        except Exception as exc:
            # 예외 문자열은 페이지·입력에서 온 것을 담을 수 있다 — 에이전트에게는 싣지 않는다
            log.error("mcp.tool_error", error=type(exc).__name__, application_id=None, run_id=None)
            return _error("도구 실행 중 내부 오류")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=reply.content)], isError=not reply.ok
        )

    return server


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], isError=True
    )


class McpEndpoint:
    """`MCP_PATH` 의 ASGI 앱. `running()` 을 앱 lifespan 에서 연다."""

    def __init__(self) -> None:
        self._server = _build_server()
        self._manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        # 매니저는 한 번만 run 할 수 있다 — 같은 앱의 lifespan 이 다시 돌면(테스트) 새로 만든다.
        # Host·Origin(DNS 리바인딩)은 앱 보안 미들웨어가 이미 본다 — SDK 쪽 검사를 겹치지 않는다
        manager = StreamableHTTPSessionManager(self._server, json_response=True, stateless=True)
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = bearer_token(Headers(scope=scope))
        if _run_tokens(scope).resolve(token) is None:
            # 설치 토큰으로는 열리지 않는다(교차 거부). 401 이면 MCP 클라이언트가 OAuth 를 찾는다
            log.warning("mcp.denied", application_id=None, run_id=None)
            await error_response(403, "invalid_token", "run 토큰이 없거나 만료됐다")(
                scope, receive, send
            )
            return
        manager = self._manager
        if manager is None:  # lifespan 밖(기동 전·종료 중)
            await error_response(503, "unavailable", "MCP 가 떠 있지 않다")(scope, receive, send)
            return
        await manager.handle_request({**scope, _SCOPE_TOKEN: token}, receive, send)
