"""PlaywrightBrowserHost 기동 순서(channel → 설치 경로 → ChromeNotFound)·잠금·기본 프로필 거부.

브라우저 없이 가짜 Playwright 로 본다. 실제 브라우저 쿠키 유지는 test_playwright_host_native.py.
"""

import os
import sys
from pathlib import Path, PurePosixPath

import pytest
from playwright.async_api import Error as PlaywrightError

from auto_apply.adapters.browser.chrome_paths import default_user_data_dirs
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.adapters.browser.profile_lock import ProfileLock
from auto_apply.domain.errors import BrowserLaunchFailed, ChromeNotFound, PolicyViolation
from auto_apply.ports.browser import PageHandle

EXE = PurePosixPath("/opt/custom/chrome")


class FakePage:
    def is_closed(self) -> bool:
        return False


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    def on(self, event, handler) -> None:
        pass

    async def new_page(self) -> FakePage:
        self.pages.append(FakePage())
        return self.pages[-1]

    async def close(self) -> None:
        pass


class FakePlaywright:
    """`launch_persistent_context` 호출 인자를 기록하고, 정한 경로에서 실패한다."""

    def __init__(self, *, fail_channel=False, fail_exe=False, fail_bundled=False) -> None:
        self.chromium = self
        self.calls: list[dict] = []
        self.stopped = 0
        self._fail = {"channel": fail_channel, "exe": fail_exe, "bundled": fail_bundled}

    async def launch_persistent_context(self, profile, **kw) -> FakeContext:
        self.calls.append(kw)
        kind = "channel" if kw["channel"] else "exe" if kw["executable_path"] else "bundled"
        if self._fail[kind]:
            raise PlaywrightError(f"{kind} launch failed\n=== logs ===\nsecret-ish detail")
        return FakeContext()

    async def stop(self) -> None:
        self.stopped += 1


def host_with(pw: FakePlaywright, profile: Path, *, exe=EXE, **kw) -> PlaywrightBrowserHost:
    async def start():
        return pw

    return PlaywrightBrowserHost(profile, find_executable=lambda: exe, start_playwright=start, **kw)


@pytest.fixture
def profile(tmp_path) -> Path:
    return tmp_path / "chrome-profile"


def assert_lock_free(profile: Path) -> None:
    lock = ProfileLock(profile)
    lock.acquire()
    lock.release()


async def test_installed_chrome_channel_first(profile):
    pw = FakePlaywright()
    await host_with(pw, profile).page()
    [call] = pw.calls
    assert call["channel"] == "chrome"
    assert call["executable_path"] is None
    assert call["headless"] is False  # 제품은 사람이 보는 창이다 (D6)
    # Ctrl+C 는 앱 lifespan 이 받아 정상 종료한다 — Playwright 가 Chrome 을 먼저 죽이지 않게
    assert call["handle_sigint"] is call["handle_sigterm"] is call["handle_sighup"] is False
    assert profile.is_dir()


async def test_falls_back_to_detected_install_path(profile):
    pw = FakePlaywright(fail_channel=True)
    host = host_with(pw, profile)
    await host.page()
    assert [c["executable_path"] for c in pw.calls] == [None, str(EXE)]
    assert pw.calls[1]["channel"] is None
    assert host.running


async def test_chrome_not_found_when_nothing_installed(profile):
    pw = FakePlaywright(fail_channel=True)
    host = host_with(pw, profile, exe=None)
    with pytest.raises(ChromeNotFound, match="설치"):
        await host.page()
    assert not host.running
    assert pw.stopped == 1
    assert_lock_free(profile)  # 못 띄웠으면 프로필을 붙잡지 않는다


async def test_launch_failure_is_retryable_error_with_short_message(profile):
    pw = FakePlaywright(fail_channel=True, fail_exe=True)
    host = host_with(pw, profile)
    with pytest.raises(BrowserLaunchFailed) as info:
        await host.page()
    assert "secret-ish" not in str(info.value)
    assert pw.stopped == 1
    assert_lock_free(profile)


async def test_bundled_chromium_skips_chrome_detection(profile):
    pw = FakePlaywright(fail_channel=True)
    host = host_with(pw, profile, exe=None, bundled_chromium=True)
    await host.page()
    [call] = pw.calls
    assert call["channel"] is None and call["executable_path"] is None
    await host.close()
    with pytest.raises(BrowserLaunchFailed):
        await host_with(FakePlaywright(fail_bundled=True), profile, bundled_chromium=True).page()


async def test_user_default_chrome_profile_refused_before_launch():
    default = default_user_data_dirs(sys.platform, os.environ, Path.home())[0]
    pw = FakePlaywright()
    host = host_with(pw, Path(default) / "auto-apply")
    with pytest.raises(PolicyViolation):
        await host.page()
    assert pw.calls == []


async def test_stale_handle_after_close(profile):
    host = host_with(FakePlaywright(), profile)
    handle = await host.page()
    assert host.vendor_page(handle) is not None
    await host.close()
    with pytest.raises(LookupError):
        host.vendor_page(handle)
    with pytest.raises(LookupError):
        host.vendor_page(PageHandle("page-999"))
