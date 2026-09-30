"""하네스 층을 하나씩 끄면 제출이 새는가 (§A4 T2.5 수용 기준 — 각 층이 실제로 일하고 있다는 증거).

층마다 "그 층만 막던" 사이트를 고르고, 그 층을 꺼 적대 스크립트를 돌려 짐 서버가
**최종 제출을 받는지** 본다. 받으면 그 층이 없을 때 test_submit_guard_native 의 "제출 0건"이
실패한다는 뜻이다. 페이지 스크립트
층과 네트워크 층은 폼 제출을 둘 다 막으므로(겹방어), 각자 **혼자서도** 막는지 따로 본다.
"""

import asyncio

import pytest

from auto_apply.adapters.browser import playwright_guard
from auto_apply.contracts.browser_tools import ToolError
from auto_apply.contracts.click import SafeBasis, SafeClick
from auto_apply.services import submit_guard
from tests.guard_rig import Rig, _rig, rig
from tests.gym.adversary import press_everything

__all__ = ["_rig", "rig"]
pytestmark = [pytest.mark.native, pytest.mark.asyncio(loop_scope="module")]
FORM_SITES = ("multipart_form", "request_submit", "iframe_form", "multi_step_form")


def _no_l2(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(submit_guard, "classify_click", lambda d: SafeClick(basis=SafeBasis.SAFE_WORD))


def _no_network(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(playwright_guard, "request_verdict", lambda **kw: None)


def _no_page_script(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(playwright_guard, "guard_script", lambda token: "undefined")


def _no_strict_tail(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(submit_guard, "STRICT_TAIL_S", 0.0)


def _no_l4(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(playwright_guard, "dialog_verdict", lambda kind: "accept")


async def _run(rig: Rig, site: str) -> list[str]:
    toolbox = rig.toolbox()
    try:
        await press_everything(toolbox, rig.gym.entry_url(site))
    finally:
        await toolbox.close()  # 끈 층의 init script 가 다음 테스트로 새지 않게 등록을 걷는다
    await asyncio.sleep(1.5)
    return [r.path for r in rig.gym.final_submissions(site)]


@pytest.mark.parametrize(
    ("off", "site"),
    [
        (_no_l2, "spa_fetch"),  # 분류가 없으면 "지원하기" 가 relaxed 로 fetch 제출
        (_no_network, "spa_fetch"),  # strict 여도 route 가 없으면 fetch 가 나간다
        (_no_network, "get_submit"),  # 쿼리를 싣는 GET 탐색
        (_no_network, "delayed_submit"),
        (_no_l4, "confirm_next"),  # "다음" 은 안전 어휘 — confirm 수락이면 곧장 제출
    ],
    ids=["L2", "L3-network", "L3-get", "L3-delayed", "L4"],
)
async def test_turning_one_layer_off_lets_a_submission_through(rig, monkeypatch, off, site):
    off(monkeypatch)
    assert await _run(rig, site), (
        f"{off.__name__} 를 꺼도 {site} 가 막혔다 — 그 층을 시험하지 못한다"
    )


@pytest.mark.parametrize("tail_off", [False, True], ids=["tail-on", "tail-off"])
async def test_strict_tail_keeps_a_delayed_submit_out_of_the_next_relaxed_window(
    rig, monkeypatch, tail_off
):
    if tail_off:
        _no_strict_tail(monkeypatch)
    toolbox = rig.toolbox()
    await toolbox.call("navigate", {"url": rig.gym.entry_url("delayed_submit")})
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    refs = {n.name: n.ref for n in snap.nodes if n.ref}
    # 2.5초 뒤 제출을 예약한다 — 창(보통 0.5초 안팎) 밖에서 터지게
    assert (await toolbox.call("click", {"ref": refs["지원하기"]})).ok
    # 곧바로 안전한 동작 — 꼬리가 있으면 3초까지 strict 로 기다렸다가 창을 연다
    await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": {"kind": "user"}})
    await asyncio.sleep(3.0)
    submitted = rig.gym.final_submissions("delayed_submit")
    await toolbox.close()
    assert bool(submitted) is tail_off


@pytest.mark.parametrize("site", FORM_SITES)
async def test_page_script_layer_alone_blocks_form_submission(rig, monkeypatch, site):
    _no_network(monkeypatch)
    assert await _run(rig, site) == []


@pytest.mark.parametrize("site", FORM_SITES)
async def test_network_layer_alone_blocks_form_submission(rig, monkeypatch, site):
    _no_page_script(monkeypatch)
    assert await _run(rig, site) == []


async def test_both_form_layers_off_lets_form_submission_through(rig, monkeypatch):
    _no_network(monkeypatch)
    _no_page_script(monkeypatch)
    assert await _run(rig, "request_submit")


async def test_turning_l5_off_misses_the_completion_page(rig, monkeypatch):
    monkeypatch.setattr(submit_guard, "completion_evidence", lambda *a: None)
    toolbox = rig.toolbox()
    await toolbox.call("navigate", {"url": rig.gym.entry_url("complete_page")})
    done = rig.gym.url("/sites/complete_page/complete.html")
    result = await toolbox.call("navigate", {"url": done})
    await toolbox.close()
    assert result.error is not ToolError.INCIDENT and toolbox.incident is None
