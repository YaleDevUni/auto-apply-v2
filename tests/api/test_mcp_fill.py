"""MCP 로 온 도구 호출이 그 run 의 BrowserToolbox·FillLog·한도로 간다 (§A5·§A6, T3.5).

대역 브라우저 위의 fill run 을 CLI 런타임처럼 MCP 로 돌린다 — 끝나면 토큰이 폐기되고 제출은 0건.
"""

import json

import pytest

from auto_apply.contracts.agent import AgentLimits
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.runner.fill_session import FillSession
from tests.api.mcp_kit import OverMcp, mcp_session, serve
from tests.runner.fill_kit import FORM, PROFILE, FillRig, fill_form_script, scripted


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path / "rig")
    yield rig
    await rig.host.close()


@pytest.fixture
async def app(tmp_path):
    async for app in serve(tmp_path):
        yield app


def _ref(snapshot_text: str, name: str) -> str:
    nodes = json.loads(snapshot_text)["snapshot"]["nodes"]
    return next(n["ref"] for n in nodes if n.get("name") == name)


async def test_mcp_and_in_process_calls_share_one_fill_log(rig, app):
    record = await rig.apps.get(await rig.app_in(S.QUEUED))
    toolbox = rig.make_toolbox(record, "run_1", {})
    session = FillSession(toolbox, AgentLimits(), lambda: 0.0)
    tokens = app.state.container.run_tokens
    try:
        await session.call("navigate", {"url": FORM})
        snap = await session.call("snapshot", {})
        name = _ref(snap.content, "이름")
        await session.call("fill", {"ref": name, "value": "홍", "source": PROFILE})
        async with tokens.open(session.call) as token, mcp_session(app, token) as s:
            res = await s.call_tool("fill", {"ref": name, "value": "홍길동", "source": PROFILE})
            assert not res.isError, res.content[0].text
            bad = await s.call_tool("fill", {"ref": name})  # 잘못된 인자 — 에러 답, run 은 계속
            assert bad.isError and json.loads(bad.content[0].text)["error"]
    finally:
        await toolbox.close()
    assert [e.value for e in toolbox.fill_log.entries] == ["홍", "홍길동"]
    assert session.calls == 5  # MCP 호출도 같은 한도 계수에 든다


async def test_mcp_calls_past_run_limit_do_not_reach_toolbox(rig, app):
    record = await rig.apps.get(await rig.app_in(S.QUEUED))
    toolbox = rig.make_toolbox(record, "run_1", {})
    session = FillSession(toolbox, AgentLimits(max_tool_calls=1), lambda: 0.0)
    try:
        tokens = app.state.container.run_tokens
        async with tokens.open(session.call) as token, mcp_session(app, token) as s:
            await s.call_tool("navigate", {"url": FORM})
            over = await s.call_tool("snapshot", {})
    finally:
        await toolbox.close()
    assert over.isError and json.loads(over.content[0].text)["error"] == "run_limit"
    assert session.calls == 1


async def test_fill_run_over_mcp_reaches_awaiting_approval(rig, app):
    tokens = app.state.container.run_tokens
    runtime = OverMcp(scripted(fill_form_script()), tokens, app)
    application_id, job = await rig.queued()
    profile = Profile(user_id="local", name="홍길동", email="hong@example.com")
    await rig.handler(runtime, profile=profile)(job)

    assert await rig.state(application_id) is S.AWAITING_APPROVAL
    run = await rig.run_record(application_id)
    review = await rig.artifacts.load_review(run.run_id)
    assert review is not None and [e.value for e in review.fill_log.entries] == ["홍길동"]
    assert rig.final_submits() == []  # 제출 0건
    # run 이 끝나면 토큰은 폐기된다
    assert tokens.live_count == 0 and tokens.resolve(runtime.tokens_seen[0]) is None
