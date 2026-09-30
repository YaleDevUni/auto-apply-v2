"""SubmitGuard 를 실제 브라우저로 — 짐 전 픽스처에서 FILL 단계 제출 0건 (§A4 T2.5 수용 기준).

"제출 0건"은 브라우저가 아니라 짐 서버가 **받은 요청**으로 단언한다(tests/gym/server.py).
Chrome 기동이
테스트 시간의 대부분이라 모듈에서 한 번만 띄우고, 테스트마다 탭을 닫아 새 탭에서 시작한다.
"""

import asyncio

import pytest

from auto_apply.contracts.browser_tools import ToolError
from auto_apply.contracts.click import ClickRisk, RiskyClick
from tests.guard_rig import Rig, _rig, rig
from tests.gym.adversary import press_everything
from tests.gym.manifest import load_manifest

__all__ = ["_rig", "rig"]
pytestmark = [pytest.mark.native, pytest.mark.asyncio(loop_scope="module")]
MANIFEST = load_manifest()
SITES = sorted(MANIFEST.sites)
# 차단된 beacon 인데도 페이지가 스스로 "지원이 완료되었습니다" 를 띄운다 — 하네스는 막았다는 것과
# 다른 통로로 새지 않았다는 것을 구분하지 못하므로 INCIDENT 로 멈춘다(닫힌 쪽, §A4 L5).
# 받아들인 위험(accepted_risk, D17)의 사이트는 제출이 나가고 L5 가 멈춘다.
_INCIDENT_EXPECTED = {"beacon"} | {n for n, s in MANIFEST.sites.items() if s.accepted_risk}


async def _settled(rig: Rig) -> None:
    await asyncio.sleep(1.5)  # 지연 제출·beacon 이 서버에 늦게 닿는지까지 본다


@pytest.mark.parametrize("site", SITES)
async def test_pressing_everything_submits_nothing(rig, site):
    toolbox = rig.toolbox()
    trace = await press_everything(toolbox, rig.gym.entry_url(site))
    await toolbox.close()
    await _settled(rig)
    assert (toolbox.incident is not None) is (site in _INCIDENT_EXPECTED), trace.results
    if MANIFEST.sites[site].accepted_risk:  # 신호 없는 "다음" 최종 제출 — 한 건 나가고 즉시 멈춘다
        assert len(rig.gym.final_submissions(site)) == 1
        return
    assert rig.gym.final_submissions() == [], (site, trace.results)
    # 하네스가 막거나 거절한 것이 없으면 적대 스크립트가 제출 경로를 건드리지도 못한 것이다
    assert trace.engaged, [(t, n) for t, n, _ in trace.results]


async def test_multi_step_saves_pass_but_the_final_step_is_blocked(rig):
    trace = await press_everything(rig.toolbox(), rig.gym.entry_url("multi_step"))
    await _settled(rig)
    assert [r.path for r in rig.gym.intermediate_requests("multi_step")] == [
        "/api/multi_step/save", "/api/multi_step/save",
    ]  # fmt: skip
    assert trace.errors(ToolError.SUBMIT_BLOCKED) == ["제출하기"]
    assert rig.gym.final_submissions() == []


async def test_type_submit_steps_pass_without_approval_up_to_the_final_button(rig):
    # D17: type=submit "다음"·"저장 후 계속" 은 승인 없이 넘기고, 최종 "제출하기" 에서 막힌다
    toolbox = rig.toolbox()
    trace = await press_everything(toolbox, rig.gym.entry_url("multi_step_form"))
    assert trace.errors(ToolError.SUBMIT_BLOCKED) == ["제출하기"]
    assert [r.path for r in rig.gym.intermediate_requests()] == [
        "/api/multi_step_form/step1", "/api/multi_step_form/step2",
    ]  # fmt: skip
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None and snap.url.endswith("/step3.html")
    ref = next(n.ref for n in snap.nodes if n.name == "제출하기")
    assert (await toolbox.call("ready_for_review", {"submit_ref": ref, "notes": "최종"})).ok
    review = toolbox.review
    assert review is not None and review.target.element.type == "submit"
    assert review.step == 3  # 페이지 단계 — 승인은 최종 제출 1회
    assert [(e.value, e.step) for e in review.fill_log.entries] == [("홍길동", 1), ("홍길동", 2)]
    assert review.target.selectors and review.target.box is not None
    await toolbox.close()
    assert rig.gym.final_submissions() == []


@pytest.mark.parametrize(
    ("site", "button", "signal"),
    [("next_final_signal", "다음", "progress:dom"), ("review_page", "계속", "no_inputs")],
)
async def test_last_step_signal_blocks_a_step_labelled_final_button(rig, site, button, signal):
    toolbox = rig.toolbox()
    trace = await press_everything(toolbox, rig.gym.entry_url(site))
    assert trace.errors(ToolError.SUBMIT_BLOCKED) == [button]
    assert len(rig.gym.intermediate_requests(site)) == 1  # 앞 단계는 승인 없이 넘겼다
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    ref = next(n.ref for n in snap.nodes if n.name == button)
    assert (await toolbox.call("ready_for_review", {"submit_ref": ref})).ok
    assert toolbox.review is not None
    assert toolbox.review.verdict == RiskyClick(reason=ClickRisk.LAST_STEP, detail=signal)
    await toolbox.close()
    await _settled(rig)
    assert rig.gym.final_submissions() == []


async def test_step_without_a_last_step_signal_is_stopped_by_l5(rig):
    # D17 이 받아들인 위험 — 사이트엔 나갔고(짐이 받았다) run 은 완료 화면에서 즉시 멈췄다
    toolbox = rig.toolbox()
    trace = await press_everything(toolbox, rig.gym.entry_url("next_final_nosignal"))
    assert trace.errors(ToolError.INCIDENT) == ["다음"]
    assert toolbox.incident is not None
    assert [r.path for r in rig.gym.final_submissions()] == ["/api/next_final_nosignal/submit"]


async def test_confirm_is_declined(rig):
    trace = await press_everything(rig.toolbox(), rig.gym.entry_url("confirm_next"))
    await _settled(rig)
    assert trace.dialogs == ["confirm"]
    assert rig.gym.final_submissions() == []


async def test_link_to_the_form_page_passes_in_strict_but_get_submission_does_not(rig):
    trace = await press_everything(rig.toolbox(), rig.gym.entry_url("apply_link"))
    form = "/sites/apply_link/form.html"
    assert [r for r in rig.gym.requests if r.path == form]  # "지원하기" 링크는 폼으로 갔다
    assert trace.errors(ToolError.SUBMIT_BLOCKED) == ["제출"]
    trace = await press_everything(rig.toolbox(), rig.gym.entry_url("get_submit"))
    assert trace.errors(ToolError.SUBMIT_BLOCKED) == ["지원하기"]
    [blocked] = [b for _, n, r in trace.results if n == "지원하기" for b in r.guard.blocked]
    assert (blocked.reason, blocked.method) == ("strict_get_query", "GET")
    assert "홍길동" not in blocked.model_dump_json()  # 막힌 요청은 출처만 싣는다
    assert rig.gym.final_submissions() == []


async def test_reaching_a_completion_page_is_an_incident(rig):
    toolbox = rig.toolbox()
    assert (await toolbox.call("navigate", {"url": rig.gym.entry_url("complete_page")})).ok
    done = rig.gym.url("/sites/complete_page/complete.html")
    assert (await toolbox.call("navigate", {"url": done})).error is ToolError.INCIDENT
    assert toolbox.incident is not None
    assert (await toolbox.call("snapshot")).error is ToolError.INCIDENT


async def test_filled_value_matches_the_dom(rig):
    toolbox = rig.toolbox()
    await toolbox.call("navigate", {"url": rig.gym.entry_url("spa_fetch")})
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    refs = {n.name: n.ref for n in snap.nodes if n.ref}
    for name, value in (("이름", "홍길동\n"), ("이메일", " hong@example.com\n")):
        args = {"ref": refs[name], "value": value, "source": {"kind": "user"}}
        assert (await toolbox.call("fill", args)).ok
    page = rig.host.vendor_page(await rig.host.page())
    dom = [await page.get_by_label(n).input_value() for n in ("이름", "이메일")]
    assert [e.value for e in toolbox.fill_log.entries] == dom == ["홍길동", "hong@example.com"]


async def test_ending_the_run_right_after_a_risky_click_keeps_the_delayed_submit_blocked(rig):
    toolbox = rig.toolbox()
    await toolbox.call("navigate", {"url": rig.gym.entry_url("delayed_submit")})
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    ref = next(n.ref for n in snap.nodes if n.name == "지원하기")
    await toolbox.call("click", {"ref": ref})  # 2.5초 뒤 제출을 예약했다
    assert (await toolbox.call("ready_for_review", {"submit_ref": ref})).ok
    await toolbox.close()  # strict 꼬리(3초) 동안 기다린 뒤 끈다 — 타이머는 그 안에 막힌다
    await asyncio.sleep(3.0)
    assert rig.gym.final_submissions() == []
