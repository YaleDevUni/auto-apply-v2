"""fill run 을 API 런타임으로 — 같은 BrowserToolbox 도구를 MCP 없이 in-process 로 (§A6, T3.8).

가짜 Messages API 가 짐 폼을 채우는 tool_use 를 돌려주고(snapshot 의 ref 는 받은 tool_result
에서 고른다),
도구 결과로 AWAITING_APPROVAL 에 간다. 최종 제출은 0건.
"""

from collections.abc import Callable

import pytest

from auto_apply.adapters.agent.scripted import ScriptStep
from auto_apply.contracts.agent import ToolReply
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.services.browser_toolbox_specs import TOOLS
from tests.adapters.anthropic_kit import Calls, FakeMessagesApi, Turn, api_runtime
from tests.runner.fill_kit import FillRig, fill_form_script


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path)
    yield rig
    await rig.host.close()


def _as_turn(step: ScriptStep) -> Callable[[list[str]], Calls | str]:
    def turn(results: list[str]) -> Calls | str:
        replies = [ToolReply(ok=True, content=c) for c in results]
        call = step(replies) if callable(step) else step
        return "" if call is None else [(call.name, call.args)]

    return turn


async def test_api_runtime_fills_the_gym_form_to_awaiting_approval(rig, tmp_path):
    turns: list[Turn] = [_as_turn(s) for s in fill_form_script()]
    api = FakeMessagesApi(turns)
    app, job = await rig.queued()
    await rig.handler(api_runtime(api, tmp_path / "runs"))(job)

    assert await rig.state(app) is S.AWAITING_APPROVAL
    assert rig.final_submits() == []  # 제출 0건
    first = api.requests[0]
    assert {t["name"] for t in first["tools"]} == set(TOOLS)  # 도구 목록 = TOOLS 그대로
    assert "https://jobs.example.com/p/1" in first["system"]
    assert len(api.requests) == 4  # navigate·snapshot·fill·ready_for_review(done — 더 묻지 않는다)
