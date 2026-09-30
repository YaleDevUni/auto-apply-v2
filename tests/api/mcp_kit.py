"""MCP(HTTP) 테스트 공용 — 앱을 lifespan 째 띄우고 SDK 클라이언트로 붙는다 (§A5).

`OverMcp` 는 CLI 런타임(T3.6)이 할 일을 흉내 낸다: run() 안에서 run 토큰을 열고, 도구 호출을
프로세스 안이 아니라 MCP 로 보낸다. `done` 은 MCP 로 오지 않으므로 토큰에 건 `call_tool` 을
감싸서 본다.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from auto_apply.api.main import create_app
from auto_apply.config import Settings
from auto_apply.contracts.agent import AgentLimits, AgentOutcome, AgentTool, ToolReply
from auto_apply.ports.agent import AgentRuntime, CallTool
from auto_apply.services.run_tokens import RunTokens

LOCAL = "http://127.0.0.1:8765"
MCP_URL = f"{LOCAL}/mcp"


def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        repository="memory",
    )


async def serve(tmp_path) -> AsyncIterator[FastAPI]:
    """앱 lifespan 을 한 태스크에서 연다 — MCP 매니저의 task group 은 들어간 태스크에서
    나와야 하는데 pytest-asyncio 픽스처는 준비와 정리를 다른 태스크에서 돈다(uvicorn 은 한 태스크).
    """
    app = create_app(settings(tmp_path))
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        waiter.cancel()
        task.result()  # 기동 실패를 그대로 올린다
    try:
        yield app
    finally:
        stop.set()
        await task


def http(app: FastAPI, headers: Mapping[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=LOCAL, headers=dict(headers or {})
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def mcp_session(app: FastAPI, token: str) -> AsyncIterator[ClientSession]:
    async with (
        http(app, bearer(token)) as client,
        streamable_http_client(MCP_URL, http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def reply_of(result) -> ToolReply:
    return ToolReply(ok=not result.isError, content=result.content[0].text)


class OverMcp:
    """다른 런타임(Scripted)의 도구 호출을 MCP 로 보낸다."""

    def __init__(self, inner: AgentRuntime, tokens: RunTokens, app: FastAPI) -> None:
        self._inner, self._tokens, self._app = inner, tokens, app
        self.tokens_seen: list[str] = []

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        done = False

        async def observed(name: str, args: Mapping[str, object]) -> ToolReply:
            nonlocal done
            reply = await call_tool(name, args)
            done = done or reply.done
            return reply

        async with (
            self._tokens.open(observed) as token,
            mcp_session(self._app, token) as session,
        ):
            self.tokens_seen.append(token)

            async def via_mcp(name: str, args: Mapping[str, object]) -> ToolReply:
                result = await session.call_tool(name, dict(args))
                return reply_of(result).model_copy(update={"done": done})

            return await self._inner.run(system_prompt, tools, via_mcp, limits=limits)
