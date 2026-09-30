"""SubmitGuard (§A4 L2~L5) — 대역 드라이버 위에서 도구가 하네스 창을 거치는지.

실제 브라우저로 같은 것을 보는 것은 test_submit_guard_native.py(짐 전 픽스처)다. 여기는 규칙의
배선 — 모드 결정·창 사이 유지·strict 꼬리·대화상자 기록·사후 감지·ready_for_review·닫힌 쪽 실패 —
을 빠르게 본다. 페이지는 tests/guard_fake_pages.py.
"""

from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.click import RiskyClick
from auto_apply.domain.submit_guard_policy import BlockReason
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.guard_fake_pages import DONE, FORM, NEXT, B, guard_sites
from tests.toolbox_kit import FakeDocuments

USER = {"kind": "user"}


@pytest.fixture
async def host(tmp_path: Path):
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    yield host
    await host.close()


@pytest.fixture
def driver(host) -> FakeGuardedPageDriver:
    return FakeGuardedPageDriver(host, guard_sites())


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def toolbox(host, driver, slept) -> BrowserToolbox:
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return BrowserToolbox(
        host, driver, FakeDocuments({}), human_gate=InMemoryHumanGate(), user_id="local",
        application_id="app_g", run_id="run_g",
        forbidden_origins=["http://127.0.0.1:8000"], step=2, sleep=sleep,
    )  # fmt: skip


async def _open(toolbox: BrowserToolbox, url: str = FORM) -> dict[str, str]:
    assert (await toolbox.call("navigate", {"url": url})).ok
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    return {n.name: n.ref for n in snap.nodes if n.ref}


def _blocked(result: ToolResult, *reasons: BlockReason) -> None:
    assert (result.ok, result.error) == (False, ToolError.SUBMIT_BLOCKED), result
    assert [b.reason for b in result.guard.blocked] == list(reasons)


async def _click(toolbox: BrowserToolbox, refs: dict[str, str], name: str) -> ToolResult:
    return await toolbox.call("click", {"ref": refs[name]})


async def test_safe_click_runs_relaxed_and_step_save_passes(toolbox, driver):
    refs = await _open(toolbox)
    result = await _click(toolbox, refs, "다음")
    assert result.ok and result.guard.blocked == ()
    assert driver.sent == [("POST", f"{B}/api/save")]


async def test_risky_click_is_strict_and_blocked(toolbox, driver):
    refs = await _open(toolbox)
    _blocked(await _click(toolbox, refs, "지원하기"), BlockReason.STRICT_NON_GET)
    assert ("POST", f"{B}/api/submit") not in driver.sent


async def test_form_submission_is_blocked_by_the_page_layer(toolbox, driver):
    refs = await _open(toolbox)
    _blocked(await _click(toolbox, refs, "제출"), BlockReason.FORM_SUBMIT)
    assert driver.sent == []


async def test_confirm_is_declined_and_reported_without_identifiers(toolbox, driver):
    refs = await _open(toolbox)
    result = await _click(toolbox, refs, "저장")  # 안전 어휘 → relaxed: L4 만이 막는다
    assert result.ok
    [dialog] = result.guard.dialogs
    assert (dialog.kind, dialog.accepted) == ("confirm", False)
    assert "900101-1234567" not in dialog.message
    assert driver.sent == []


async def test_delayed_submission_after_a_strict_click_stays_blocked(toolbox, driver, slept):
    refs = await _open(toolbox)
    assert (await _click(toolbox, refs, "늦게")).ok  # 창 안에서는 아무것도 안 보냈다
    # 다음 동작(relaxed)은 strict 꼬리를 기다린 뒤 연다 — 그 사이 타이머의 제출은 strict 로 막힌다
    result = await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": USER})
    assert result.ok
    assert [b.reason for b in result.guard.blocked] == [BlockReason.STRICT_NON_GET]
    assert slept and slept[-1] > 0
    assert driver.sent == []


async def test_check_goes_through_click_classification(toolbox, driver):
    refs = await _open(toolbox)
    args = {"ref": refs["지원 내용에 동의"], "on": True, "source": USER}
    _blocked(await toolbox.call("check", args), BlockReason.STRICT_NON_GET)
    # 입력 자체는 됐으니 기록된다 — 에이전트는 실패로 알게 된다
    assert toolbox.fill_log.entries[-1].checked is True
    args = {"ref": refs["뉴스레터"], "on": True, "source": USER}
    assert (await toolbox.call("check", args)).ok  # 안전한 체크박스의 저장 POST 는 통과
    assert driver.sent == [("POST", f"{B}/api/save")]


async def test_click_reaching_a_submit_ancestor_is_strict(toolbox, driver):
    refs = await _open(toolbox)
    _blocked(await _click(toolbox, refs, "선택"), BlockReason.STRICT_NON_GET)


async def test_query_navigation_is_blocked_in_strict_but_links_pass(toolbox, driver):
    refs = await _open(toolbox)
    _blocked(await _click(toolbox, refs, "쿼리 이동"), BlockReason.STRICT_GET_QUERY)
    assert (await _click(toolbox, refs, "다음 페이지")).ok
    assert driver.sent[-1] == ("GET", NEXT)


async def test_app_origin_requests_are_blocked(toolbox, driver):
    refs = await _open(toolbox)
    result = await _click(toolbox, refs, "앱 호출")
    assert BlockReason.FORBIDDEN_ORIGIN in [b.reason for b in result.guard.blocked]
    assert driver.sent == []


async def test_navigate_refuses_urls_carrying_typed_values(toolbox):
    refs = await _open(toolbox)
    assert (await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": USER})).ok
    result = await toolbox.call("navigate", {"url": f"{B}/api/apply?name=홍길동"})
    assert result.error is ToolError.FORBIDDEN_URL
    result = await toolbox.call("navigate", {"url": f"{B}/api/apply?rrn=900101-1234567"})
    assert result.error is ToolError.FORBIDDEN_URL and "900101" not in result.message


async def test_new_completion_text_stops_the_run(toolbox):
    refs = await _open(toolbox)
    assert (await _click(toolbox, refs, "다시 그리기")).ok  # 원래 있던 문구는 새 근거가 아니다
    result = await _click(toolbox, refs, "끝")
    assert result.error is ToolError.INCIDENT
    assert toolbox.incident is not None
    assert (await toolbox.call("snapshot")).error is ToolError.INCIDENT


async def test_reaching_a_completion_page_by_navigation_is_an_incident(toolbox):
    await _open(toolbox)
    assert (await toolbox.call("navigate", {"url": DONE})).error is ToolError.INCIDENT


async def test_guard_that_cannot_arm_refuses_every_page_tool(toolbox, driver):
    refs = await _open(toolbox)
    driver.fail_arm = True
    for tool, args in [
        ("click", {"ref": refs["다음"]}),
        ("fill", {"ref": refs["이름"], "value": "x", "source": USER}),
        ("snapshot", {}),
    ]:
        assert (await toolbox.call(tool, args)).error is ToolError.GUARD_UNAVAILABLE
    assert driver.sent == []
    assert toolbox.fill_log.entries == ()


async def test_driver_refuses_windows_without_an_armed_guard(host, driver):
    from auto_apply.domain.errors import SubmitGuardUnavailable
    from auto_apply.domain.submit_guard_policy import GuardMode

    page = await host.page()
    with pytest.raises(SubmitGuardUnavailable):
        await driver.set_window(page, GuardMode.RELAXED, carried=())


async def test_ready_for_review_records_target_steps_and_ends_the_run(toolbox):
    refs = await _open(toolbox)
    assert (await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": USER})).ok
    notes = "다단계일 수 있음 900101-1234567"
    result = await toolbox.call("ready_for_review", {"submit_ref": refs["제출"], "notes": notes})
    assert result.ok
    review = toolbox.review
    assert review is not None
    assert (review.step, review.fill_log.entries[0].step) == (2, 2)
    assert review.target.element.name == "제출" and review.target.page_url == FORM
    assert isinstance(review.verdict, RiskyClick)
    assert "900101-1234567" not in review.model_dump_json()
    assert (await toolbox.call("snapshot")).error is ToolError.RUN_FINISHED


async def test_close_disarms_the_guard(toolbox, driver):
    await _open(toolbox)
    assert driver.armed
    await toolbox.close()
    assert not driver.armed


async def test_filled_value_is_normalized_like_the_browser(toolbox, driver, host):
    refs = await _open(toolbox)
    args = {"ref": refs["이름"], "value": "홍길동\n", "source": USER}
    assert (await toolbox.call("fill", args)).ok
    assert toolbox.fill_log.entries[0].value == "홍길동"
    el = next(e for e in driver.document(await host.page()).elements if e.name == "이름")
    assert el.value == "홍길동"


async def test_close_waits_out_the_strict_tail_before_disarming(toolbox, driver, slept):
    refs = await _open(toolbox)
    assert (await _click(toolbox, refs, "늦게")).ok
    await toolbox.close()
    assert slept and slept[-1] > 0
    assert not driver.armed
