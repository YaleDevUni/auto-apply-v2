"""ClaudeCliAgentRuntime — done·한도·시간·취소에서 프로세스(자식 포함)가 남지 않는다 (§A6)."""

import asyncio
import json

import pytest

from auto_apply.contracts.agent import AgentEnd, AgentLimits
from auto_apply.services.run_tokens import RunTokens
from tests.adapters.agent_cli.fake_kit import (
    TOOLS,
    Calls,
    all_gone,
    runtime,
    seen,
    wait_for,
)


async def test_done_reply_stops_the_process(tmp_path, out, monkeypatch):
    tokens, calls = RunTokens(), Calls(done=True)
    rt = runtime(tmp_path, tokens, "hang", monkeypatch)
    task = asyncio.create_task(rt.run("s", TOOLS, calls, limits=AgentLimits()))
    await wait_for(out / "pids")
    via_mcp = tokens.resolve(seen(out)["env"]["AUTO_APPLY_RUN_TOKEN"])
    assert via_mcp is not None
    reply = await via_mcp("navigate", {})
    assert reply.done
    outcome = await asyncio.wait_for(task, 10)
    assert outcome.ended is AgentEnd.COMPLETED and outcome.tool_calls == 1
    await all_gone(out)


async def test_calls_past_the_limit_never_reach_the_tool(tmp_path, out, monkeypatch):
    tokens, calls = RunTokens(), Calls()
    rt = runtime(tmp_path, tokens, "hang", monkeypatch)
    task = asyncio.create_task(rt.run("s", TOOLS, calls, limits=AgentLimits(max_tool_calls=1)))
    await wait_for(out / "pids")
    via_mcp = tokens.resolve(seen(out)["env"]["AUTO_APPLY_RUN_TOKEN"])
    assert via_mcp is not None
    assert (await via_mcp("navigate", {})).ok
    over = await via_mcp("snapshot", {})
    assert not over.ok and json.loads(over.content)["error"] == "tool_limit"
    outcome = await asyncio.wait_for(task, 10)
    assert outcome.ended is AgentEnd.TOOL_LIMIT and calls.calls == ["navigate"]
    await all_gone(out)


async def test_time_limit_kills_the_process(tmp_path, out, monkeypatch):
    rt = runtime(tmp_path, RunTokens(), "hang", monkeypatch)
    outcome = await rt.run("s", TOOLS, Calls(), limits=AgentLimits(max_seconds=1.5))
    assert outcome.ended is AgentEnd.TIME_LIMIT
    await all_gone(out)


async def test_cancel_leaves_no_process(tmp_path, out, monkeypatch):
    tokens = RunTokens()
    rt = runtime(tmp_path, tokens, "hang", monkeypatch)
    task = asyncio.create_task(rt.run("s", TOOLS, Calls(), limits=AgentLimits()))
    await wait_for(out / "pids")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await all_gone(out)
    assert tokens.live_count == 0


async def test_tools_stay_closed_until_init_is_checked(tmp_path, out, monkeypatch):
    tokens, calls = RunTokens(), Calls()
    rt = runtime(tmp_path, tokens, "silent", monkeypatch)
    task = asyncio.create_task(rt.run("s", TOOLS, calls, limits=AgentLimits()))
    await wait_for(out / "seen.json")
    via_mcp = tokens.resolve(seen(out)["env"]["AUTO_APPLY_RUN_TOKEN"])
    assert via_mcp is not None
    refused = await via_mcp("navigate", {})
    assert not refused.ok and calls.calls == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
