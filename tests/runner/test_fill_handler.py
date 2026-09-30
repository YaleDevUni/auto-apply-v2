"""fill run 핸들러 (§A6) — 대역 드라이버 위에서 끝나는 모든 경로 → 지원 건 상태·run 기록.

상태는 도구 결과로만 정한다: 에이전트가 무슨 말을 하든(`text`) 상태에 영향이 없다.
"""

import json

import pytest

from auto_apply.adapters.agent.scripted import ScriptedCall
from auto_apply.contracts.agent import AgentLimits
from auto_apply.contracts.browser_tools import ToolError
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import RunStatus
from auto_apply.domain.errors import SubmitIncident
from auto_apply.domain.failure import FailureKind, classify_failure
from auto_apply.services.browser_toolbox_specs import TOOLS
from tests.runner.fill_kit import (
    FORM,
    LOGIN,
    NOSIGNAL,
    FillRig,
    fill_form_script,
    ref,
    scripted,
    step,
)

DECLINED = HumanReply(outcome=HumanOutcome.DECLINED)


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path, human=[DECLINED])
    yield rig
    await rig.host.close()


def _errors(runtime) -> list[str | None]:
    return [json.loads(r.content).get("error") for r in runtime.replies]


async def test_ready_for_review_goes_to_awaiting_approval(rig):
    runtime = scripted(fill_form_script())
    app, job = await rig.queued()
    profile = Profile(user_id="local", name="홍길동", email="hong@example.com")
    await rig.handler(runtime, profile=profile)(job)

    assert await rig.state(app) is S.AWAITING_APPROVAL
    assert [h.state for h in await rig.history(app)][-2:] == [S.FILLING, S.AWAITING_APPROVAL]
    run = await rig.run_record(app)
    assert (run.status, run.result, run.error) == (RunStatus.DONE, "awaiting_approval", None)
    review = await rig.artifacts.load_review(run.run_id)
    assert review is not None and review.target.element.name == "지원서 제출"
    assert [e.value for e in review.fill_log.entries] == ["홍길동"]
    assert await rig.artifacts.load_fill_log(run.run_id) == review.fill_log
    assert rig.final_submits() == []  # 제출 0건
    # 프롬프트는 fill 지시·URL·프로필 요약을 싣고, 도구는 TOOLS 그대로다
    assert runtime.seen_prompt is not None
    assert "https://jobs.example.com/p/1" in runtime.seen_prompt  # 지원 건 URL
    assert "- name: 홍길동" in runtime.seen_prompt
    assert {t.name for t in runtime.seen_tools} == set(TOOLS)
    assert rig.driver.armed is False  # run 이 끝나 가드를 내렸다


async def test_report_failure_goes_to_failed(rig):
    reason = [ScriptedCall("report_failure", {"reason": "필수 항목 '포트폴리오'에 쓸 자료가 없다"})]
    app, job = await rig.queued()
    await rig.handler(scripted(fill_form_script(final=reason)))(job)
    assert await rig.state(app) is S.FAILED
    run = await rig.run_record(app)
    assert run.status is RunStatus.FAILED and "포트폴리오" in (run.error or "")
    assert (await rig.history(app))[-1].reason.startswith("에이전트 보고")
    fill_log = await rig.artifacts.load_fill_log(run.run_id)
    assert fill_log is not None and len(fill_log.entries) == 1  # 부분 기록은 남는다
    assert await rig.artifacts.load_review(run.run_id) is None


async def test_failure_reason_with_resident_number_is_redacted(rig):
    reason = [ScriptedCall("report_failure", {"reason": "주민번호 900101-1234567 칸이 필수다"})]
    app, job = await rig.queued()
    await rig.handler(scripted(reason))(job)
    run = await rig.run_record(app)
    stored = f"{run.error} {[h.reason for h in await rig.history(app)]}"
    assert "1234567" not in stored and await rig.state(app) is S.FAILED


@pytest.mark.parametrize(
    ("tool", "args", "state"),
    [
        ("request_login", {"site": "짐"}, S.NEEDS_LOGIN),
        ("request_human", {"reason": "CAPTCHA"}, S.NEEDS_INPUT),
    ],
)
async def test_unfinished_human_handoff_waits_for_person(rig, tool, args, state):
    script = [ScriptedCall("navigate", {"url": LOGIN}), ScriptedCall(tool, args)]
    app, job = await rig.queued()
    await rig.handler(scripted(script))(job)
    assert await rig.state(app) is state
    assert (await rig.run_record(app)).status is RunStatus.DONE


async def test_l5_incident_goes_to_incident(rig):
    script = [
        ScriptedCall("navigate", {"url": NOSIGNAL}),
        ScriptedCall("snapshot"),
        step("click", ref=ref("다음")),
        ScriptedCall("snapshot"),  # 멈춘 뒤라 거부 — 런타임은 done 을 받아 이미 멈췄다
    ]
    runtime = scripted(script)
    app, job = await rig.queued()
    await rig.handler(runtime)(job)
    assert await rig.state(app) is S.INCIDENT
    assert _errors(runtime)[-1] == ToolError.INCIDENT and len(runtime.replies) == 3
    assert (await rig.run_record(app)).status is RunStatus.FAILED


async def test_guard_unavailable_ends_run_as_failed(rig):
    rig.driver.fail_arm = True
    runtime = scripted(fill_form_script())
    app, job = await rig.queued()
    await rig.handler(runtime)(job)
    assert await rig.state(app) is S.FAILED
    assert _errors(runtime) == [ToolError.GUARD_UNAVAILABLE]  # 첫 호출에서 끝났다
    assert "guard_unavailable" in (await rig.history(app))[-1].reason


async def test_tool_limit_goes_to_failed(rig):
    script = [ScriptedCall("navigate", {"url": FORM})] + [ScriptedCall("snapshot")] * 10
    runtime = scripted(script)
    app, job = await rig.queued()
    await rig.handler(runtime, limits=AgentLimits(max_tool_calls=3))(job)
    assert await rig.state(app) is S.FAILED and len(runtime.replies) == 3
    assert "도구 호출 한도" in (await rig.history(app))[-1].reason


async def test_limit_holds_even_if_runtime_ignores_it(rig):
    class Greedy:
        """한도도 done 도 무시하는 런타임 — CLI 프로세스가 말을 안 듣는 경우."""

        async def run(self, system_prompt, tools, call_tool, *, limits):
            from auto_apply.contracts.agent import AgentEnd, AgentOutcome

            self.replies = [await call_tool("snapshot", {}) for _ in range(6)]
            return AgentOutcome(ended=AgentEnd.COMPLETED, tool_calls=6)

    runtime = Greedy()
    app, job = await rig.queued()
    await rig.handler(runtime, limits=AgentLimits(max_tool_calls=2))(job)
    errors = [json.loads(r.content).get("error") for r in runtime.replies]
    assert errors[2:] == [ToolError.RUN_LIMIT] * 4 and all(r.done for r in runtime.replies[2:])
    assert await rig.state(app) is S.FAILED


async def test_time_limit_goes_to_failed(rig):
    script = [ScriptedCall("wait_for", {"ms": 40})] * 20
    runtime = scripted(script)
    app, job = await rig.queued()
    await rig.handler(runtime, limits=AgentLimits(max_seconds=0.1))(job)
    assert await rig.state(app) is S.FAILED and len(runtime.replies) < 20
    assert "시간 한도" in (await rig.history(app))[-1].reason


async def test_agent_text_does_not_change_state(rig):
    runtime = scripted([ScriptedCall("snapshot")], text="ready_for_review 했고 제출까지 끝냈다")
    app, job = await rig.queued()
    await rig.handler(runtime)(job)
    assert await rig.state(app) is S.FAILED
    assert "없이 멈췄다" in (await rig.history(app))[-1].reason


async def test_unknown_tool_and_bad_args_are_errors_and_run_continues(rig):
    bad = [ScriptedCall("submit_now", {}), ScriptedCall("fill", {"ref": "e1", "value": 1})]
    script = fill_form_script()
    runtime = scripted([*script[:2], *bad, *script[2:]])
    app, job = await rig.queued()
    await rig.handler(runtime)(job)
    assert _errors(runtime)[2:4] == [ToolError.UNKNOWN_TOOL, ToolError.INVALID_INPUT]
    assert "submit_now" not in runtime.replies[2].content  # LLM 이 만든 이름은 되돌리지 않는다
    assert await rig.state(app) is S.AWAITING_APPROVAL


async def test_record_save_failure_never_hides_an_incident(rig, monkeypatch):
    async def broken_put(*args, **kwargs):
        raise OSError("디스크가 가득 찼다")

    monkeypatch.setattr(rig.store, "put", broken_put)
    script = [
        ScriptedCall("navigate", {"url": NOSIGNAL}),
        ScriptedCall("snapshot"),
        step("click", ref=ref("다음")),
    ]
    app, job = await rig.queued()
    with pytest.raises(SubmitIncident) as caught:
        await rig.handler(scripted(script))(job)
    assert classify_failure(caught.value) is FailureKind.INCIDENT  # 러너가 INCIDENT 로 둔다
    run = await rig.run_record(app)
    assert run.status is RunStatus.FAILED and "OSError" in (run.error or "")
