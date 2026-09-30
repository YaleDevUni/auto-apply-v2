"""양성 대조(native) — 하네스 없이 사람처럼 조작하면 모든 짐 사이트에서 제출이 기록된다.

이게 통과해야 T2.5 의 "제출 0건"이 "원래 제출될 게 막혔다"는 뜻이 된다.
"""

import asyncio

import pytest

from tests.gym.drivers import DRIVERS

pytestmark = pytest.mark.native


async def _wait_for(gym, predicate) -> None:
    assert await asyncio.to_thread(gym.wait_for, predicate), "시간 안에 서버가 요청을 받지 못했다"


@pytest.mark.parametrize("site", sorted(DRIVERS))
async def test_unguarded_driver_submits_exactly_once(gym, gym_browser, site):
    context = await gym_browser.new_context()
    try:
        page = await context.new_page()
        await DRIVERS[site](page, gym.entry_url(site))
        await _wait_for(gym, lambda: gym.final_submissions(site))
        await asyncio.sleep(0.2)  # 중복 제출이 뒤늦게 오는지 본다
    finally:
        await context.close()

    assert len(gym.final_submissions(site)) == 1
    assert gym.final_submissions() == gym.final_submissions(site)
    assert gym.unexpected_requests() == []


@pytest.mark.parametrize(
    ("site", "steps"),
    [
        ("multi_step", ["/api/multi_step/save", "/api/multi_step/save"]),
        ("multi_step_form", ["/api/multi_step_form/step1", "/api/multi_step_form/step2"]),
    ],
)
async def test_multi_step_saves_pass_through_before_final(gym, gym_browser, site, steps):
    context = await gym_browser.new_context()
    try:
        await DRIVERS[site](await context.new_page(), gym.entry_url(site))
        await _wait_for(gym, lambda: gym.final_submissions(site))
    finally:
        await context.close()
    posts = [r.path for r in gym.requests if r.method == "POST"]
    assert posts == [*steps, f"/api/{site}/submit"]
    assert [r.path for r in gym.intermediate_requests(site)] == steps


async def test_open_form_button_itself_sends_nothing(gym, gym_browser):
    context = await gym_browser.new_context()
    try:
        page = await context.new_page()
        await page.goto(gym.entry_url("open_form_button"))
        await page.get_by_role("button", name="지원하기").click()
        await page.get_by_label("이름").wait_for()
    finally:
        await context.close()
    assert [r for r in gym.requests if r.method != "GET"] == []


async def test_login_wall_accepts_injected_cookie(gym, gym_browser):
    # T2.6 은 사람 대신 쿠키를 심어 재개한다 — 그 경로가 짐에서 실제로 통하는지
    site = gym.manifest.sites["login_wall"]
    context = await gym_browser.new_context()
    try:
        page = await context.new_page()
        await page.goto(gym.entry_url("login_wall"))
        assert page.url.endswith(site.login.page)
        await context.add_cookies([{"name": site.login.cookie, "value": "1", "url": gym.base_url}])
        await page.goto(gym.entry_url("login_wall"))
        assert page.url == gym.entry_url("login_wall")
        await page.get_by_role("button", name="지원하기").wait_for()
    finally:
        await context.close()
    assert gym.intermediate_requests() == [] and gym.final_submissions() == []
