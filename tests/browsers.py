"""native 테스트가 띄우는 실제 BrowserHost (M2 공통).

제품 기본은 설치된 Chrome(D6)이고, Chrome 없는 CI 는 `AUTO_APPLY_TEST_BROWSER=chromium` 으로
Playwright 번들 Chromium 을 쓴다. 테스트 창이 작업을 가리지 않게 headless 로 띄우고, 프로필은 늘
테스트 임시 디렉터리다 — 사용자 기본 프로필은 쓰지 않는다.
"""

import asyncio
import contextlib
import os
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.ports.browser import PageHandle

TEST_BROWSER_ENV = "AUTO_APPLY_TEST_BROWSER"


def real_host(profile_dir: Path) -> PlaywrightBrowserHost:
    bundled = os.environ.get(TEST_BROWSER_ENV, "chrome").strip().lower() == "chromium"
    return PlaywrightBrowserHost(profile_dir, headless=True, bundled_chromium=bundled)


async def quit_like_human(host: PlaywrightBrowserHost, handle: PageHandle) -> None:
    """사람이 브라우저를 종료한 것처럼 — 호스트를 거치지 않고 브라우저 프로세스가 스스로 끝난다."""
    page = host.vendor_page(handle)
    closed = asyncio.Event()
    page.context.on("close", lambda _: closed.set())
    cdp = await page.context.new_cdp_session(page)
    # 브라우저가 응답 전에 끝나면 send 가 에러를 낼 수 있다 — 기다릴 것은 close 이벤트다.
    with contextlib.suppress(PlaywrightError):
        await cdp.send("Browser.close")
    await asyncio.wait_for(closed.wait(), timeout=30)


async def close_tabs_like_human(host: PlaywrightBrowserHost, handle: PageHandle) -> None:
    """사람이 탭을 전부 닫았다 — 브라우저 프로세스는 남는다."""
    for page in list(host.vendor_page(handle).context.pages):
        await page.close()
