"""짐 pytest 픽스처. 짐 밖의 테스트(T2.5 등)는 자기 conftest 에서 이 모듈을 import 해 쓴다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from playwright.async_api import Browser, async_playwright

from tests.gym.browser import launch_test_browser
from tests.gym.server import GymServer


@pytest.fixture
def gym() -> Iterator[GymServer]:
    """127.0.0.1 임의 포트의 짐 서버 — 테스트마다 새로 띄워 기록이 섞이지 않게."""
    with GymServer() as server:
        yield server


@pytest.fixture
async def gym_browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        browser = await launch_test_browser(pw)
        try:
            yield browser
        finally:
            await browser.close()
