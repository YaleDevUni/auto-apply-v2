"""짐 테스트용 브라우저 기동 — 제품 기본은 설치된 Chrome(D6), CI 는 Playwright 번들 Chromium.

사용자 프로필을 건드리지 않도록 영속 프로필이 아닌 일회용 브라우저를 띄운다.
양성 대조는 "하네스 없이"여야 하므로 BrowserHost(tests/browsers.py) 를 거치지 않고
Playwright 를 직접 쓴다 — 환경변수 규약만 같다.
"""

from __future__ import annotations

import os

from playwright.async_api import Browser, Playwright

TEST_BROWSER_ENV = "AUTO_APPLY_TEST_BROWSER"


async def launch_test_browser(pw: Playwright) -> Browser:
    kind = os.environ.get(TEST_BROWSER_ENV, "chrome").strip().lower()
    if kind == "chromium":
        return await pw.chromium.launch(headless=True)
    if kind == "chrome":
        return await pw.chromium.launch(channel="chrome", headless=True)
    raise ValueError(f"{TEST_BROWSER_ENV} 는 chrome 또는 chromium 이어야 한다: {kind!r}")
