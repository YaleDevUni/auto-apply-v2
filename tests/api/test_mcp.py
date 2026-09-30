"""BrowserToolbox MCP(HTTP) 노출 (§A5·§A10, T3.5) — run 토큰으로만 열리고 설치 토큰과 교차 안 함."""

import inspect
import json
from collections.abc import Mapping

import pytest
from fastapi import FastAPI

from auto_apply.api.security import TOKEN_HEADER
from auto_apply.contracts.agent import ToolReply
from auto_apply.services.browser_toolbox_specs import TOOLS
from auto_apply.services.run_tokens import RunTokens
from tests.api.mcp_kit import bearer, http, mcp_session, serve

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class Recorder:
    """run 하나의 call_tool 대역 — 무엇이 이 run 으로 왔는지 센다."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, name: str, args: Mapping[str, object]) -> ToolReply:
        self.calls.append((name, dict(args)))
        return ToolReply(ok=True, content=json.dumps({"run": self.label}))


@pytest.fixture
async def app(tmp_path):
    async for app in serve(tmp_path):
        yield app


def _tokens(app: FastAPI) -> RunTokens:
    return app.state.container.run_tokens


async def _post_init(app: FastAPI, headers: Mapping[str, str]):
    async with http(app, {**MCP_HEADERS, **headers}) as client:
        return await client.post("/mcp", json=INIT)


async def test_list_tools_is_exactly_toolbox_specs(app):
    async with _tokens(app).open(Recorder("a")) as token, mcp_session(app, token) as s:
        tools = (await s.list_tools()).tools
    assert {t.name for t in tools} == set(TOOLS)
    for t in tools:
        assert t.inputSchema == TOOLS[t.name].input_model.model_json_schema()


async def test_call_goes_to_that_runs_call_tool_only(app):
    a, b = Recorder("a"), Recorder("b")
    tokens = _tokens(app)
    async with tokens.open(a) as ta, tokens.open(b) as tb:
        async with mcp_session(app, ta) as s:
            res = await s.call_tool("snapshot", {})
        assert json.loads(res.content[0].text) == {"run": "a"} and not res.isError
        async with mcp_session(app, tb) as s:
            await s.call_tool("scroll", {"direction": "down"})
    assert a.calls == [("snapshot", {})]
    assert b.calls == [("scroll", {"direction": "down"})]
    assert tokens.live_count == 0


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param(bearer("x" * 43), id="unknown-token"),
        pytest.param({"Authorization": "Basic Zm9vOmJhcg=="}, id="not-bearer"),
    ],
)
async def test_mcp_rejects_without_live_run_token(app, headers):
    res = await _post_init(app, headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "invalid_token"


async def test_expired_token_is_rejected_after_run_ends(app):
    async with _tokens(app).open(Recorder("a")) as token:
        assert (await _post_init(app, bearer(token))).status_code == 200
    assert (await _post_init(app, bearer(token))).status_code == 403


async def test_other_runs_token_stops_working_when_that_run_ends(app):
    tokens = _tokens(app)
    async with tokens.open(Recorder("a")) as ta:
        async with tokens.open(Recorder("b")) as tb:
            pass
        assert (await _post_init(app, bearer(tb))).status_code == 403
        assert (await _post_init(app, bearer(ta))).status_code == 200


async def test_install_token_does_not_open_mcp(app):
    session = app.state.container.session_token
    for headers in (bearer(session), {TOKEN_HEADER: session}):
        res = await _post_init(app, headers)
        assert res.status_code == 403 and res.json()["error"]["code"] == "invalid_token"


async def test_run_token_does_not_open_rest_api(app):
    async with _tokens(app).open(Recorder("a")) as token, http(app) as client:
        for res in (
            await client.get("/api/session", headers=bearer(token)),
            await client.get("/api/profile", headers=bearer(token)),
            await client.get("/health", headers=bearer(token)),
            await client.put("/api/profile", json={"name": "x"}, headers=bearer(token)),
            await client.get("/api/profile", headers={TOKEN_HEADER: token}),
            await client.put("/api/profile", json={"name": "x"}, headers={TOKEN_HEADER: token}),
        ):
            assert res.status_code == 403, res.request.url
            assert res.json()["error"]["code"] == "invalid_token"


async def test_mcp_keeps_host_and_origin_checks(app):
    async with _tokens(app).open(Recorder("a")) as token:
        evil_host = {**bearer(token), "Host": "evil.example:8765"}
        assert (await _post_init(app, evil_host)).json()["error"]["code"] == "host_not_allowed"
        evil_origin = {**bearer(token), "Origin": "https://evil.example"}
        assert (await _post_init(app, evil_origin)).json()["error"]["code"] == "origin_not_allowed"


async def test_tool_exception_is_error_without_its_text(app):
    async def boom(name: str, args: Mapping[str, object]) -> ToolReply:
        raise RuntimeError("900101-1234567 비밀 내용")

    async with _tokens(app).open(boom) as token, mcp_session(app, token) as s:
        res = await s.call_tool("snapshot", {})
    assert res.isError
    assert "900101" not in res.content[0].text and "비밀" not in res.content[0].text


async def test_token_revoked_mid_request_does_not_reach_tool(app, monkeypatch):
    rec = Recorder("a")
    tokens = _tokens(app)
    async with tokens.open(rec) as token, mcp_session(app, token) as s:
        # 입구 검사는 통과했는데 도구 직전(핸들러 call_tool)에 run 이 끝난 경우
        def resolve(token):
            return None if inspect.stack()[1].function == "call_tool" else rec

        monkeypatch.setattr(tokens, "resolve", resolve)
        res = await s.call_tool("snapshot", {})
    assert res.isError and rec.calls == []


async def test_run_tokens_are_revoked_on_error_and_reject_junk():
    tokens = RunTokens()
    with pytest.raises(RuntimeError):
        async with tokens.open(Recorder("a")) as token:
            assert tokens.resolve(token) is not None
            raise RuntimeError
    assert tokens.resolve(token) is None
    assert [tokens.resolve(t) for t in (None, "", "토큰", token[:-1])] == [None] * 4
    assert tokens.live_count == 0
