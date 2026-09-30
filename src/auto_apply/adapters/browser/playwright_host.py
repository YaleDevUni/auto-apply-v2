"""설치된 Chrome 을 앱 전용 프로필로 띄우는 BrowserHost (§A1, D6).

`channel="chrome"` 을 먼저 쓰고, 못 띄우면 표준 설치 경로를 직접 찾아 `executable_path` 로 띄운다.
Playwright 번들 Chromium(`bundled_chromium=True`)은 Chrome 없는 CI·테스트용이다(M2 공통).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from itertools import count
from pathlib import Path, PurePath

import structlog
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from auto_apply.adapters.browser.chrome_paths import ensure_not_default_profile, find_chrome
from auto_apply.adapters.browser.profile_lock import ProfileLock
from auto_apply.domain.errors import BrowserLaunchFailed, ChromeNotFound
from auto_apply.ports.browser import PageHandle

log = structlog.get_logger(__name__)
_CLOSE_TIMEOUT_S = 15


async def _start_playwright() -> Playwright:
    return await async_playwright().start()


def _first_line(e: BaseException) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0]


class PlaywrightBrowserHost:
    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        bundled_chromium: bool = False,
        find_executable: Callable[[], PurePath | None] = find_chrome,
        start_playwright: Callable[[], Awaitable[Playwright]] = _start_playwright,
    ) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._bundled = bundled_chromium
        self._find_executable = find_executable
        self._start_playwright = start_playwright
        self._lock = ProfileLock(profile_dir)
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._context_closed = False
        self._pages: dict[str, Page] = {}
        self._current: str | None = None
        # 재기동해도 핸들 id 가 되풀이되지 않게 호스트 수명 동안 이어서 센다 (port 계약).
        self._ids = count(1)
        # 동시에 들어온 첫 사용이 브라우저를 두 번 띄우지 않게 (두 번째는 프로필 잠금에 걸린다).
        self._guard = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._context is not None and not self._context_closed

    async def page(self) -> PageHandle:
        async with self._guard:
            return await self._page()

    async def _page(self) -> PageHandle:
        if not self.running:
            await self._launch()
        context = self._context
        assert context is not None
        current = self._pages.get(self._current or "")
        if current is not None and not current.is_closed():
            return PageHandle(self._current or "")
        open_pages = [p for p in context.pages if not p.is_closed()]
        page = open_pages[-1] if open_pages else await context.new_page()
        handle = self._handle_for(page)
        self._current = handle.id
        return handle

    def vendor_page(self, handle: PageHandle) -> Page:
        """핸들 → Playwright 페이지. adapters/browser 안에서만 쓴다(도구 구현, §A5).

        닫힌 탭이나 재기동 전 핸들이면 LookupError.
        """
        page = self._pages.get(handle.id)
        if page is None or page.is_closed():
            raise LookupError(f"열린 탭이 아니다: {handle.id}")
        return page

    async def close(self) -> None:
        async with self._guard:
            await self._shutdown()
            self._lock.release()

    def _handle_for(self, page: Page) -> PageHandle:
        for key, known in self._pages.items():
            if known is page:
                return PageHandle(key)
        key = f"page-{next(self._ids)}"
        self._pages[key] = page
        return PageHandle(key)

    async def _launch(self) -> None:
        await self._shutdown()  # 사람이 닫은 브라우저의 Playwright 드라이버를 치운다
        profile = self._profile_dir.resolve()
        ensure_not_default_profile(profile)
        self._lock.acquire()
        try:
            profile.mkdir(parents=True, exist_ok=True)
            pw = await self._start_playwright()
            try:
                context = await self._open_context(pw, profile)
            except BaseException:
                await pw.stop()
                raise
        except BaseException:
            # 못 띄웠으면 프로필을 쓰는 것이 없다 — 잠금을 쥐고 있을 이유가 없다.
            self._lock.release()
            raise
        self._pw, self._context, self._context_closed = pw, context, False
        context.on("close", lambda _: self._on_context_closed(context))
        log.info("browser.launched", application_id=None, run_id=None, profile=str(profile))

    def _on_context_closed(self, context: BrowserContext) -> None:
        if context is self._context:
            self._context_closed = True
            log.info("browser.closed", application_id=None, run_id=None)

    async def _open_context(self, pw: Playwright, profile: Path) -> BrowserContext:
        executable: PurePath | None = None
        if not self._bundled:
            try:
                return await self._launch_persistent(pw, profile, channel="chrome")
            except PlaywrightError as first:
                executable = self._find_executable()
                if executable is None:
                    raise ChromeNotFound(
                        "Chrome 을 찾을 수 없다 — Google Chrome 을 설치한 뒤 다시 시도하라 "
                        f"({_first_line(first)})"
                    ) from first
        try:
            return await self._launch_persistent(pw, profile, executable=executable)
        except PlaywrightError as e:
            raise BrowserLaunchFailed(f"브라우저를 띄우지 못했다 — {_first_line(e)}") from e

    async def _launch_persistent(
        self,
        pw: Playwright,
        profile: Path,
        *,
        channel: str | None = None,
        executable: PurePath | None = None,
    ) -> BrowserContext:
        # 신호 처리는 앱이 한다: Ctrl+C 에 Playwright 가 Chrome 을 바로 죽이면 lifespan 종료의
        # 정상 close 전에 쿠키가 디스크에 안 써질 수 있다. 창 크기는 사람이 조절한다(headful).
        # 서비스 워커가 보내는 요청은 하네스 route 를 비껴갈 수 있어 막는다 (§A4 L3).
        return await pw.chromium.launch_persistent_context(
            profile,
            channel=channel,
            executable_path=str(executable) if executable is not None else None,
            headless=self._headless,
            no_viewport=True,
            service_workers="block",
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
        )

    async def _shutdown(self) -> None:
        context, pw = self._context, self._pw
        self._context, self._pw, self._pages, self._current = None, None, {}, None
        if context is not None:
            # 이미 사람이 닫았거나 프로세스가 죽었으면 닫을 것이 없다. 멈춘 브라우저가 앱 종료를
            # 붙잡지 않게 기다림에 상한을 둔다 — 드라이버를 멈추면(pw.stop) 브라우저도 끝난다.
            with contextlib.suppress(PlaywrightError, TimeoutError):
                await asyncio.wait_for(context.close(), _CLOSE_TIMEOUT_S)
        if pw is not None:
            await pw.stop()
