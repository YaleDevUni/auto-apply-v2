"""실제 `claude` 로 도는 테스트 공용 — 127.0.0.1 임의 포트 서빙·echo MCP·지시 프롬프트 (§A6).

CLI 는 다른 프로세스라 ASGI 전송이 아니라 진짜 포트가 필요하다. 모델은 값싼 쪽을 쓴다
(`AUTO_APPLY_TEST_CLAUDE_MODEL`).
"""

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from auto_apply.adapters.agent.claude_cli import ClaudeCliAgentRuntime
from auto_apply.api.main import create_app
from auto_apply.config import Settings
from auto_apply.contracts.agent import AgentLimits, AgentOutcome, AgentTool
from auto_apply.ports.agent import CallTool
from auto_apply.services.run_tokens import RunTokens

MODEL = os.environ.get("AUTO_APPLY_TEST_CLAUDE_MODEL", "claude-haiku-4-5")


def _bind() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


@asynccontextmanager
async def _serve(app: ASGIApp, sock: socket.socket) -> AsyncIterator[int]:
    server = uvicorn.Server(uvicorn.Config(app, lifespan="on", log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.02)
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        await task
        sock.close()


@asynccontextmanager
async def serving(app: ASGIApp) -> AsyncIterator[int]:
    """앱을 lifespan 째 127.0.0.1 임의 포트에 띄우고 포트를 준다."""
    async with _serve(app, _bind()) as port:
        yield port


@asynccontextmanager
async def serving_app(cfg: Settings) -> AsyncIterator[FastAPI]:
    """콘솔 스크립트처럼 포트를 먼저 잡고 그 포트로 앱을 만들어 띄운다 — bootstrap 이 조립한
    런타임(`container.agent`)이 바로 이 앱의 /mcp 를 가리킨다 (T3.7)."""
    sock = _bind()
    app = create_app(cfg, port=sock.getsockname()[1])
    async with _serve(app, sock):
        yield app


def echo_mcp_app(tokens: RunTokens, tools: Sequence[AgentTool]) -> Starlette:
    """계약 테스트용 MCP — 주어진 도구를 내놓고 호출은 run 토큰이 가리키는 call_tool 로."""
    server: Server[Any, Any] = Server("auto_apply")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
                for t in tools]  # fmt: skip

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        scope: Scope = server.request_context.request.scope  # type: ignore[union-attr]
        call = tokens.resolve(_bearer(scope))
        if call is None:
            reply_text, is_error = "run 이 끝났다", True
        else:
            reply = await call(name, arguments)
            reply_text, is_error = reply.content, not reply.ok
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=reply_text)], isError=is_error
        )

    manager = StreamableHTTPSessionManager(server, json_response=True, stateless=True)

    class Endpoint:  # 함수가 아닌 ASGI 앱이어야 Route 가 request 로 감싸지 않는다
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if tokens.resolve(_bearer(scope)) is None:
                await JSONResponse({"error": "invalid_token"}, 403)(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(routes=[Route("/mcp", endpoint=Endpoint())], lifespan=lifespan)


def _bearer(scope: Scope) -> str | None:
    scheme, _, value = Headers(scope=scope).get("authorization", "").partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def cli_runtime(tokens: RunTokens, port: int, runs_dir: Path, *, human_wait_s: float = 5.0):
    return ClaudeCliAgentRuntime(
        tokens.open,
        mcp_url=f"http://127.0.0.1:{port}/mcp",
        runs_dir=runs_dir,
        human_wait_s=human_wait_s,
        model=MODEL,
    )


class Directed:
    """계약 테스트의 "이 순서로 부르고 싶다"를 시스템 프롬프트 지시로 바꿔 CLI 에 준다."""

    def __init__(self, inner: ClaudeCliAgentRuntime, intent: Sequence[tuple[str, Mapping]]) -> None:
        self._inner = inner
        lines = [
            f"{i}. {name} — arguments 객체: {json.dumps(dict(args))}"
            for i, (name, args) in enumerate(intent, 1)
        ]
        self._prompt = (
            "너는 테스트 로봇이다. 아래 도구 호출을 적힌 순서대로, 한 번에 하나씩 부른다."
            " arguments 는 적힌 JSON 객체를 키·값·타입까지 그대로 쓴다(숫자는 숫자로)."
            " 결과가 에러여도 다음으로 넘어간다. 병렬 호출 금지. 목록이 끝나면 도구를 더"
            " 부르지 말고 '끝' 이라고만 답한다.\n" + "\n".join(lines)
        )

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        return await self._inner.run(self._prompt, tools, call_tool, limits=limits, run_id=run_id)


def transcript_events(runs_dir: Path) -> list[dict[str, Any]]:
    (path,) = runs_dir.glob("*/transcript.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
