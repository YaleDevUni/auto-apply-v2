"""GuardedPageDriver contract test (§A4, ports/browser.py).

대역과 실제(native)에 같은 기대를 건다. 같은 도구 테스트 페이지(tests/fixtures/toolbox/form.html ↔
toolbox_pages.fake_sites)를 쓰고, 실제 쪽은 "서버가 받은 요청"으로도 확인한다.
"""

import pytest

from auto_apply.domain.errors import PageActionFailed, PageFailure, SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import BlockReason, GuardMode
from tests.toolbox_kit import DriverKit, _shared_real_kit, driver_kit
from tests.toolbox_pages import FORM, NEXT

__all__ = ["_shared_real_kit", "driver_kit"]
pytestmark = pytest.mark.asyncio(loop_scope="module")
APP = ("http://127.0.0.1:8000",)


async def _armed(kit: DriverKit, path: str = FORM):
    page = await kit.host.page()
    await kit.driver.navigate(page, kit.url(path))
    await kit.driver.arm(page, forbidden_origins=APP)
    snap = await kit.driver.snapshot(page)
    refs = {n.name: n.ref for n in snap.nodes if n.ref}
    return page, refs


def _posts(kit: DriverKit) -> list[str]:
    if kit.gym is None:
        return [u for m, u in getattr(kit.driver, "sent", []) if m != "GET"]
    return [r.path for r in kit.gym.requests if r.method not in ("GET", "HEAD")]


async def test_windows_and_clicks_need_an_armed_guard(driver_kit):
    page, refs = await _armed(driver_kit)
    await driver_kit.driver.disarm(page)
    with pytest.raises(SubmitGuardUnavailable):
        await driver_kit.driver.set_window(page, GuardMode.RELAXED, carried=())
    with pytest.raises(SubmitGuardUnavailable):
        await driver_kit.driver.click(page, refs["다음 페이지"])
    assert (await driver_kit.driver.snapshot(page)).url == driver_kit.url(FORM)


async def test_strict_click_on_a_submit_button_is_blocked(driver_kit):
    page, refs = await _armed(driver_kit)
    d = driver_kit.driver
    await d.drain(page)
    await d.set_window(page, GuardMode.STRICT, carried=())
    await d.click(page, refs["지원하기"])
    await d.settle(page)
    report = await d.drain(page)
    assert BlockReason.FORM_SUBMIT in [b.reason for b in report.blocked]
    assert (await d.drain(page)).blocked == ()  # 수거하면 비워진다
    assert _posts(driver_kit) == []
    assert (await d.snapshot(page)).url == driver_kit.url(FORM)


async def test_relaxed_link_click_navigates(driver_kit):
    page, refs = await _armed(driver_kit)
    d = driver_kit.driver
    await d.set_window(page, GuardMode.RELAXED, carried=())
    await d.click(page, refs["다음 페이지"])
    await d.settle(page)
    assert (await d.read_text(page)).urls[0] == driver_kit.url(NEXT)
    assert (await d.drain(page)).blocked == ()


async def test_describe_reads_the_submit_button_from_the_dom(driver_kit):
    page, refs = await _armed(driver_kit)
    [button, *_] = await driver_kit.driver.describe(page, refs["지원하기"])
    assert (button.tag, button.type, button.name) == ("button", "submit", "지원하기")
    assert button.in_form and button.is_form_default_button and not button.in_dialog
    [link, *_] = await driver_kit.driver.describe(page, refs["다음 페이지"])
    assert (link.tag, link.href) == ("a", "next.html")


async def test_click_refuses_file_inputs_and_secret_fields(driver_kit):
    page, refs = await _armed(driver_kit)
    d = driver_kit.driver
    await d.set_window(page, GuardMode.RELAXED, carried=())
    with pytest.raises(PageActionFailed) as e:
        await d.click(page, refs["이력서"])
    assert e.value.reason is PageFailure.UNSUPPORTED_ELEMENT
    with pytest.raises(PageActionFailed) as e:
        await d.click(page, refs["비밀번호"])
    assert e.value.reason is PageFailure.SECRET_FIELD


async def test_read_text_and_submit_target(driver_kit):
    page, refs = await _armed(driver_kit)
    d = driver_kit.driver
    text = await d.read_text(page)
    assert text.urls[0] == driver_kit.url(FORM)
    assert "지원서" in text.lines
    target = await d.submit_target(page, refs["지원하기"])
    assert target.element.name == "지원하기"
    assert target.page_url == driver_kit.url(FORM) and target.frame_index == 0
    assert target.selectors  # 승인 뒤 같은 요소를 다시 찾을 후보가 있다 (§A4 L6)
