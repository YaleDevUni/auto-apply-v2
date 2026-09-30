"""실제 `claude` 가 60초 넘게 걸리는 ask_user 호출을 끊지 않고 답을 받는다 (native, §A6, T3.7).

ask_user 는 최대 HUMAN_WAIT_S 동안 MCP 호출 하나를 열어 둔다. CLI 의 MCP 호출이 기본 타임아웃(60초
언저리)에 끊기면 사람이 늦게 답한 질문이 전부 실패한다 — 그래서 bootstrap 이 조립한 런타임
(`MCP_TOOL_TIMEOUT = human_wait_s + 60초`)으로 앱의 진짜 /mcp 를 거쳐 70초 대기를 실측한다.
브라우저는 쓰지 않는다(도구 실행은 테스트가 대신한다).
"""

import asyncio
import json
from collections.abc import Mapping

import pytest

from auto_apply.adapters.agent.claude_cli import ClaudeCliAgentRuntime
from auto_apply.config import Settings
from auto_apply.contracts.agent import AgentEnd, AgentLimits, ToolReply
from auto_apply.services.browser_toolbox_specs import agent_tools
from tests.adapters.agent_cli.kit import MODEL, Directed, serving_app, transcript_events

pytestmark = [pytest.mark.native, pytest.mark.timeout(400)]
WAIT_S = 70.0
ANSWER = "백엔드 3년 — 늦은 답"


async def test_ask_user_waiting_past_60s_is_not_cut_off(tmp_path):
    cfg = Settings(
        data_dir=tmp_path / "data", storage="memory", repository="memory", guide_source="static",
        llm_provider="claude_cli", claude_cli_model=MODEL, human_wait_s=120, agent_max_seconds=300,
    )  # fmt: skip
    waited: list[float] = []

    async def slow_human(name: str, args: Mapping[str, object]) -> ToolReply:
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(WAIT_S)  # 사람이 늦게 답한다
        waited.append(loop.time() - start)
        body = {"tool": name, "ok": True, "answer": {"text": ANSWER}}
        return ToolReply(ok=True, content=json.dumps(body, ensure_ascii=False))

    async with serving_app(cfg) as app:
        c = app.state.container
        assert isinstance(c.agent, ClaudeCliAgentRuntime)
        ask = {"question": "경력 요약을 알려 주세요", "field_hint": "경력 요약"}
        runtime = Directed(c.agent, [("ask_user", ask)])
        limits = AgentLimits(max_tool_calls=3, max_seconds=cfg.agent_max_seconds)
        outcome = await runtime.run("", agent_tools(), slow_human, limits=limits, run_id="run_wait")
        assert c.run_tokens.live_count == 0

    assert outcome.ended is AgentEnd.COMPLETED
    assert len(waited) == 1 and waited[0] >= WAIT_S
    results = [
        block
        for e in transcript_events(cfg.runs_dir)
        if e.get("type") == "user"
        for block in e["message"]["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(results) == 1, results
    assert not results[0].get("is_error"), results[0]
    assert ANSWER in json.dumps(results[0], ensure_ascii=False)
