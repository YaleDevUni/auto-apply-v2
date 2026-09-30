"""실제 `claude` CLI e2e (native) — 앱의 MCP 로 짐 사이트를 채워 AWAITING_APPROVAL, 제출 0건 (§A6).

CLI 가 여는 것은 우리 MCP 뿐이고, 브라우저는 앱(하네스)이 띄운 Chrome 이 127.0.0.1 짐만 연다.
"""

import pytest

from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.api.main import create_app
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply
from auto_apply.contracts.jobs import JobKind, JobRecord
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.services.browser_toolbox_specs import agent_tools
from tests.adapters.agent_cli.kit import cli_runtime, serving, transcript_events
from tests.api.mcp_kit import settings
from tests.browsers import real_host
from tests.gym.fixtures import gym
from tests.runner.fill_kit import FillRig
from tests.runner.kit import T0

pytestmark = pytest.mark.native
__all__ = ["gym"]
SITE = "multi_step_form"


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path)
    host = real_host(tmp_path / "data" / "chrome-profile")
    rig.host, rig.driver = host, PlaywrightGuardedPageDriver(host)  # type: ignore[assignment]
    # 에이전트가 무엇을 묻든 사람이 같은 답을 준다 — 프로필에 없는 "경력 요약" 몫
    answer = HumanReply(outcome=HumanOutcome.DONE, answer="백엔드 3년")
    rig.gate = ScriptedHumanGate([answer] * 5)
    rig.human_wait_s = 30.0
    yield rig
    await host.close()


async def test_cli_fill_run_on_gym_reaches_approval_with_only_our_tools(rig, gym, tmp_path):
    app = create_app(settings(tmp_path / "app"))
    runs = tmp_path / "runs"
    record = await rig.apps.create(gym.entry_url(SITE))  # 짐(127.0.0.1)만 — 외부 사이트 금지
    await rig.apps.transition(record.application_id, S.QUEUED, run_id=None)
    job = JobRecord(job_id="job_cli", kind=JobKind.FILL, application_id=record.application_id,
                    created_at=T0, run_after=T0)  # fmt: skip
    profile = Profile(user_id="local", name="홍길동", email="hong@example.com")

    async with serving(app) as port:
        tokens = app.state.container.run_tokens
        runtime = cli_runtime(tokens, port, runs, human_wait_s=rig.human_wait_s)
        await rig.handler(runtime, profile=profile)(job)
        assert tokens.live_count == 0

    events = transcript_events(runs)
    init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
    assert set(init["tools"]) == {f"mcp__auto_apply__{t.name}" for t in agent_tools()}
    assert [s["name"] for s in init["mcp_servers"]] == ["auto_apply"]
    assert await rig.state(record.application_id) is S.AWAITING_APPROVAL
    assert gym.final_submissions(SITE) == []  # 제출 0건
    run = await rig.run_record(record.application_id)
    review = await rig.artifacts.load_review(run.run_id)
    assert review is not None and "홍길동" in [e.value for e in review.fill_log.entries]
