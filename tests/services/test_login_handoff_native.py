"""로그인 벽 → 사람 핸드오프를 실제 브라우저와 짐 login_wall 사이트로 (§A5, T2.6 수용 기준).

감지 → 대기(가드 꺼짐, 에이전트 도구 거부) → 사람(테스트)이 로그인 → 재개(가드 다시 섬) → 폼 도달.
"제출 0건"은 짐 서버가 받은 요청으로 단언한다. 사람의 로그인 폼 제출은 통과해야 하고, 재개 뒤
에이전트의 지원서 제출은 막혀야 한다 — 로그인 뒤 돌아온 지원 페이지는 가드가 꺼진 동안 열린 문서다.
"""

import pytest

from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTask
from auto_apply.domain.human_handoff import HandoffSignal
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.guard_rig import Rig, _rig, rig

__all__ = ["_rig", "rig"]
pytestmark = [pytest.mark.native, pytest.mark.asyncio(loop_scope="module")]
DONE = HumanReply(outcome=HumanOutcome.DONE)
SITE = "login_wall"


async def _refs(toolbox: BrowserToolbox) -> tuple[ToolResult, dict[str, str]]:
    snap = await toolbox.call("snapshot")
    assert snap.ok and snap.snapshot is not None, snap
    return snap, {n.name: n.ref for n in snap.snapshot.nodes if n.ref}


async def _at_login_wall(rig: Rig, toolbox: BrowserToolbox) -> dict[str, str]:
    # 모듈이 Chrome 프로필 하나를 같이 쓴다 — 앞 테스트의 로그인 쿠키를 지운다
    await rig.host.vendor_page(await rig.host.page()).context.clear_cookies()
    assert (await toolbox.call("navigate", {"url": rig.gym.entry_url(SITE)})).ok
    snap, refs = await _refs(toolbox)
    assert snap.snapshot is not None and snap.snapshot.url.endswith("/login.html")
    assert snap.handoff is HandoffSignal.PASSWORD_FIELD  # 감지
    return refs


async def _submit_is_blocked(rig: Rig, toolbox: BrowserToolbox) -> None:
    snap, refs = await _refs(toolbox)
    assert snap.handoff is None and snap.snapshot is not None
    assert snap.snapshot.url == rig.gym.entry_url(SITE)  # 폼 도달
    fill = {"ref": refs["이름"], "value": "홍길동", "source": {"kind": "profile", "key": "name"}}
    assert (await toolbox.call("fill", fill)).ok
    result = await toolbox.call("click", {"ref": refs["지원하기"]})
    assert result.error is ToolError.SUBMIT_BLOCKED, result
    assert rig.gym.final_submissions() == []


async def test_detect_wait_cookie_resume_reaches_the_form(rig):
    site = rig.gym.manifest.sites[SITE]
    assert site.login is not None

    async def human(_: HumanTask) -> HumanReply:
        page = rig.host.vendor_page(await rig.host.page())
        await page.context.add_cookies(
            [{"name": site.login.cookie, "value": "1", "url": rig.gym.base_url}]
        )
        return DONE

    toolbox = rig.toolbox(ScriptedHumanGate([human]))
    refs = await _at_login_wall(rig, toolbox)
    password = {"ref": refs["비밀번호"], "value": "hunter2", "source": {"kind": "user"}}
    assert (await toolbox.call("fill", password)).error is ToolError.SECRET_FIELD
    assert (await toolbox.call("request_login", {"site": "짐"})).ok
    assert (await toolbox.call("navigate", {"url": rig.gym.entry_url(SITE)})).ok
    await _submit_is_blocked(rig, toolbox)
    await toolbox.close()


async def test_human_submits_the_login_form_but_the_agent_cannot_submit_the_application(rig):
    refused: list[ToolResult] = []

    async def human(_: HumanTask) -> HumanReply:
        page = rig.host.vendor_page(await rig.host.page())
        refused.append(await toolbox.call("click", {"ref": refs["로그인"]}))
        await page.get_by_label("아이디").fill("me")
        await page.get_by_label("비밀번호").fill("pw")  # 사람이 친다 — 앱은 모른다
        await page.get_by_role("button", name="로그인").click()
        await page.wait_for_url(rig.gym.entry_url(SITE))  # 사이트가 지원 페이지로 돌려보낸다
        return DONE

    toolbox = rig.toolbox(ScriptedHumanGate([human]))
    refs = await _at_login_wall(rig, toolbox)
    blocked = await toolbox.call("click", {"ref": refs["로그인"]})
    assert blocked.error is ToolError.SUBMIT_BLOCKED  # 에이전트 손으로는 로그인 폼도 안 나간다
    assert rig.gym.intermediate_requests(SITE) == []
    assert (await toolbox.call("request_login", {"site": "짐"})).ok
    assert [r.error for r in refused] == [ToolError.AWAITING_HUMAN]
    assert [r.path for r in rig.gym.intermediate_requests(SITE)] == ["/api/login_wall/login"]
    await _submit_is_blocked(rig, toolbox)
    await toolbox.close()


async def test_timeout_ends_the_run_and_leaves_the_browser_to_the_human(rig):
    toolbox = rig.toolbox(InMemoryHumanGate(), human_wait_s=0.2)
    await _at_login_wall(rig, toolbox)
    result = await toolbox.call("request_login", {"site": "짐"})
    assert result.error is ToolError.NEEDS_LOGIN
    assert (await toolbox.call("snapshot")).error is ToolError.RUN_FINISHED
    # 가드가 꺼져 있다 — 사람은 늦게라도 로그인할 수 있다
    page = rig.host.vendor_page(await rig.host.page())
    await page.get_by_label("아이디").fill("me")
    await page.get_by_role("button", name="로그인").click()
    await page.wait_for_url(rig.gym.entry_url(SITE))
    assert rig.gym.final_submissions() == []
