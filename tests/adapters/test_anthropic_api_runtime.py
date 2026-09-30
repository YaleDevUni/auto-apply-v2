"""AnthropicApiAgentRuntime — 가짜 Messages API 로 요청 모양·도구 결과·done·한도·취소·기록
(§A6, T3.8).

공통 계약(순서·거부·done·한도·시간)은 tests/ports/test_agent_runtime_contract.py 가
"api" params 로 본다.
실패 분류는 test_anthropic_api_errors.py.
"""

import asyncio
from collections.abc import Mapping

import pytest

from auto_apply.adapters.agent.anthropic_api import KICKOFF
from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentTool, ToolReply
from tests.adapters.anthropic_kit import KEY, FakeMessagesApi, api_runtime

SCHEMA: dict[str, object] = {"type": "object", "properties": {"x": {"type": "integer"}}}
TOOLS = (
    AgentTool(name="echo", description="되돌려 준다", input_schema=SCHEMA),
    AgentTool(name="stop", description="끝낸다", input_schema={"type": "object"}),
)


class Tools:
    def __init__(self, *, fail: frozenset[str] = frozenset(), done: frozenset[str] = frozenset(),
                 content: str = '{"ok": true}') -> None:  # fmt: skip
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._fail, self._done, self._content = fail, done, content

    async def __call__(self, name: str, args: Mapping[str, object]) -> ToolReply:
        self.calls.append((name, dict(args)))
        ok = name not in self._fail
        return ToolReply(ok=ok, content=self._content, done=name in self._done)


async def test_request_carries_prompt_tools_key_and_cache(tmp_path):
    api = FakeMessagesApi(["끝"])
    out = await api_runtime(api, tmp_path).run("시스템 지시", TOOLS, Tools(), limits=AgentLimits())
    assert out.ended is AgentEnd.COMPLETED and out.tool_calls == 0 and out.text == "끝"
    (body,) = api.requests
    assert body["system"] == "시스템 지시"
    assert body["model"] == "claude-sonnet-5-5"
    assert body["messages"] == [{"role": "user", "content": KICKOFF}]
    assert body["tools"] == [
        {"name": "echo", "description": "되돌려 준다", "input_schema": SCHEMA},
        {"name": "stop", "description": "끝낸다", "input_schema": {"type": "object"}},
    ]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in body  # 강제 tool_choice 는 현행 모델에서 400
    assert api.headers[0]["x-api-key"] == KEY


async def test_tool_results_go_back_with_is_error_in_one_message(tmp_path):
    api = FakeMessagesApi([[("echo", {"x": 1}), ("nope", {})], "다 했다"])
    tools = Tools(fail=frozenset({"nope"}))
    out = await api_runtime(api, tmp_path).run("s", TOOLS, tools, limits=AgentLimits())
    assert tools.calls == [("echo", {"x": 1}), ("nope", {})]  # 한 응답의 호출을 순서대로
    assert out.ended is AgentEnd.COMPLETED and out.tool_calls == 2
    second = api.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    echoed = [b for b in second[1]["content"] if b["type"] == "tool_use"]
    assert [b["id"] for b in echoed] == ["toolu_001", "toolu_002"]  # 받은 그대로 되돌림
    assert second[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_001", "content": '{"ok": true}',
         "is_error": False},
        {"type": "tool_result", "tool_use_id": "toolu_002", "content": '{"ok": true}',
         "is_error": True},
    ]  # fmt: skip
    assert out.input_tokens == 20 and out.output_tokens == 6 and out.text == "다 했다"


async def test_done_stops_mid_batch_without_another_request(tmp_path):
    api = FakeMessagesApi([[("stop", {}), ("echo", {"x": 1})], "안 온다"])
    tools = Tools(done=frozenset({"stop"}))
    out = await api_runtime(api, tmp_path).run("s", TOOLS, tools, limits=AgentLimits())
    assert tools.calls == [("stop", {})]
    assert len(api.requests) == 1
    assert out.ended is AgentEnd.COMPLETED and out.tool_calls == 1


async def test_tool_limit_mid_batch_never_reaches_the_tool(tmp_path):
    api = FakeMessagesApi([[("echo", {"x": 1}), ("echo", {"x": 2}), ("echo", {"x": 3})]])
    tools = Tools()
    limits = AgentLimits(max_tool_calls=2)
    out = await api_runtime(api, tmp_path).run("s", TOOLS, tools, limits=limits)
    assert len(tools.calls) == 2 and len(api.requests) == 1
    assert out.ended is AgentEnd.TOOL_LIMIT and out.tool_calls == 2


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "end_turn"])
async def test_tool_use_is_not_run_unless_the_model_stopped_for_it(tmp_path, stop):
    """max_tokens 에 잘린 tool_use 입력은 반쪽일 수 있다 — 에이전트가 멈춘 것으로 본다."""
    api = FakeMessagesApi([[("echo", {"x": 1})]])
    api.stop_reason = stop
    tools = Tools()
    out = await api_runtime(api, tmp_path).run("s", TOOLS, tools, limits=AgentLimits())
    assert tools.calls == [] and out.ended is AgentEnd.COMPLETED


async def test_time_limit_cuts_a_hanging_tool_call(tmp_path):
    api = FakeMessagesApi([[("echo", {"x": 1})]])

    async def hangs(name: str, args: Mapping[str, object]) -> ToolReply:
        await asyncio.Event().wait()
        raise AssertionError

    limits = AgentLimits(max_seconds=0.1)
    out = await api_runtime(api, tmp_path).run("s", TOOLS, hangs, limits=limits)
    assert out.ended is AgentEnd.TIME_LIMIT and out.tool_calls == 1
    assert api.transport.closed


async def test_timeout_error_from_a_tool_is_not_a_time_limit(tmp_path):
    api = FakeMessagesApi([[("echo", {"x": 1})]])

    async def times_out(name: str, args: Mapping[str, object]) -> ToolReply:
        raise TimeoutError("도구 안의 시간 초과")

    with pytest.raises(TimeoutError, match="도구 안의"):
        await api_runtime(api, tmp_path).run("s", TOOLS, times_out, limits=AgentLimits())


async def test_cancel_stops_the_run_and_closes_the_connection(tmp_path):
    api = FakeMessagesApi([[("echo", {"x": 1})], [("echo", {"x": 2})]])
    entered = asyncio.Event()
    calls: list[str] = []

    async def blocks(name: str, args: Mapping[str, object]) -> ToolReply:
        calls.append(name)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError

    task = asyncio.create_task(api_runtime(api, tmp_path).run("s", TOOLS, blocks,
                                                               limits=AgentLimits()))  # fmt: skip
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["echo"] and len(api.requests) == 1
    assert api.transport.closed


async def test_transcript_is_under_run_id_with_identifiers_masked(tmp_path):
    rrn = "900101-1234567"
    api = FakeMessagesApi([[("echo", {"x": 1})], "끝"])
    tools = Tools(content=f'{{"value": "{rrn}"}}')
    await api_runtime(api, tmp_path).run("s", TOOLS, tools, limits=AgentLimits(), run_id="run_7")
    text = (tmp_path / "run_7" / "transcript.jsonl").read_text(encoding="utf-8")
    assert len(text.splitlines()) == 3  # 응답·도구 결과·응답
    assert "toolu_001" in text and rrn not in text and KEY not in text


async def test_unsafe_run_id_does_not_escape_runs_dir(tmp_path):
    api = FakeMessagesApi(["끝"])
    runs = tmp_path / "runs"
    await api_runtime(api, runs).run("s", TOOLS, Tools(), limits=AgentLimits(), run_id="../x")
    (made,) = runs.iterdir()
    assert made.name.startswith("agent_") and (made / "transcript.jsonl").exists()
    assert not (tmp_path / "x").exists()
