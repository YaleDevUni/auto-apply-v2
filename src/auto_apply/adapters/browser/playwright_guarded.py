"""GuardedPageDriver — PlaywrightPageDriver 에 제출 차단 하네스를 더한 것 (§A4 L2·L3·L4·L5).

가드는 브라우저 context 마다 하나(`ContextGuard`)다. 사람이 브라우저를 닫아 context 가 바뀌면
다음 `arm()` 이 새로 건다. 클릭은 가드가 선 context 에서만 한다 — 못 서면
`SubmitGuardUnavailable`(닫힌 쪽).
"""

import asyncio
import contextlib
import secrets
from typing import Any

from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from auto_apply.adapters.browser.guard_script import (
    ALLOW_STEP,
    DESCRIBE_CLICK,
    GUARD_KEY,
    READ_PAGE,
    TARGET,
)
from auto_apply.adapters.browser.playwright_guard import ContextGuard
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.adapters.browser.playwright_pages import (
    _ACTION_TIMEOUT_MS,
    PlaywrightPageDriver,
    _act,
)
from auto_apply.contracts.click import ElementDescriptor
from auto_apply.contracts.submit_guard import GuardReport, PageText, SubmitTarget, TargetBox
from auto_apply.domain import page_elements as rules
from auto_apply.domain.errors import PageActionFailed, PageFailure, SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import GuardMode
from auto_apply.ports.browser import PageHandle

# 클릭 핸들러가 await 뒤에 보내는 요청까지 창 안에 담는 최소 대기와, 요청이 잦아들기를 기다리는
# 상한. 상한을 넘겨 늦게 오는 요청은 창 밖이지만 모드가 유지되므로(strict 뒤엔 strict) 새지 않는다.
SETTLE_MIN_S = 0.4
SETTLE_MAX_S = 3.0
_READ_TIMEOUT_S = 2.0


class PlaywrightGuardedPageDriver(PlaywrightPageDriver):
    def __init__(self, host: PlaywrightBrowserHost) -> None:
        super().__init__(host)
        self._guards: dict[BrowserContext, ContextGuard] = {}
        self._token = secrets.token_hex(16)  # 페이지가 가드를 끄거나 기록을 지우지 못하게

    # ------------------------------------------------------------------ 하네스
    async def arm(self, page: PageHandle, *, forbidden_origins: tuple[str, ...]) -> None:
        context = self._page(page).context
        guard = self._guards.get(context)
        if guard is None:
            guard = self._guards[context] = ContextGuard(context, self._token)
            context.on("close", self._forget_context)
        try:
            await guard.arm(forbidden_origins)
        except PlaywrightError as e:
            raise SubmitGuardUnavailable("제출 차단 하네스를 브라우저에 걸지 못했다") from e

    def _forget_context(self, context: BrowserContext) -> None:
        self._guards.pop(context, None)

    async def disarm(self, page: PageHandle) -> None:
        guard = self._guards.get(self._page(page).context)
        if guard is not None:
            await guard.disarm()

    async def set_window(
        self, page: PageHandle, mode: GuardMode, *, carried: tuple[str, ...]
    ) -> None:
        guard = self._ready_guard(page)
        guard.mode, guard.carried = mode, carried
        guard.window_started = asyncio.get_running_loop().time()

    async def settle(self, page: PageHandle) -> None:
        guard = self._guards.get(self._page(page).context)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_MAX_S
        await asyncio.sleep(SETTLE_MIN_S)
        # 요청 집합은 Playwright 이벤트 콜백이 바꾼다 — 짧게 들여다본다.
        while guard is not None and guard.pending_in_window() and loop.time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        with contextlib.suppress(PlaywrightError, PageActionFailed):
            remaining_ms = max(1.0, (deadline - loop.time()) * 1000)
            await self._page(page).wait_for_load_state("domcontentloaded", timeout=remaining_ms)

    async def drain(self, page: PageHandle) -> GuardReport:
        guard = self._guards.get(self._page(page).context)
        return GuardReport() if guard is None else await guard.drain()

    def _ready_guard(self, page: PageHandle) -> ContextGuard:
        guard = self._guards.get(self._page(page).context)
        if guard is None or not guard.ready:
            raise SubmitGuardUnavailable("제출 차단 하네스가 서 있지 않다")
        return guard

    # ------------------------------------------------------------------ 요소
    async def describe(self, page: PageHandle, ref: str) -> tuple[ElementDescriptor, ...]:
        element, _ = await self._element(page, ref)
        infos: list[dict[str, Any]] = await _act(element.evaluate(DESCRIBE_CLICK))
        return tuple(ElementDescriptor.model_validate(i) for i in infos)

    async def click(self, page: PageHandle, ref: str) -> None:
        guard = self._ready_guard(page)  # 하네스 없는 클릭은 없다
        element, desc = await self._element(page, ref)
        if rules.is_file_input(desc["tag"], desc["type"]):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, "파일 입력은 upload 로 넣는다")
        if guard.mode is GuardMode.STEP:  # 이 버튼의 폼 제출 한 번만 스크립트 층을 지난다 (D17)
            with contextlib.suppress(PlaywrightError):
                await element.evaluate(ALLOW_STEP, [GUARD_KEY, self._token])
        await _act(element.click(timeout=_ACTION_TIMEOUT_MS))

    async def read_text(self, page: PageHandle) -> PageText:
        p = self._page(page)
        urls: list[str] = []
        lines: list[str] = []
        inputs = submitters = 0
        progress: list[tuple[int, int]] = []
        for frame in p.frames:
            if frame.is_detached():
                continue
            urls.append(frame.url)
            with contextlib.suppress(PlaywrightError, TimeoutError, TypeError, ValueError):
                seen = await asyncio.wait_for(frame.evaluate(READ_PAGE), _READ_TIMEOUT_S)
                lines.extend(s for line in str(seen["text"]).splitlines() if (s := line.strip()))
                inputs += int(seen["inputs"])
                submitters += int(seen["submitters"])
                progress.extend((int(c), int(t)) for c, t in seen["progress"])
        return PageText(
            urls=tuple(urls), lines=tuple(lines), inputs=inputs, submitters=submitters,
            progress=tuple(progress),
        )  # fmt: skip

    async def submit_target(self, page: PageHandle, ref: str) -> SubmitTarget:
        p = self._page(page)
        element, _ = await self._element(page, ref)
        descriptors = await self.describe(page, ref)
        found: dict[str, Any] = await _act(element.evaluate(TARGET))
        frame = await element.owner_frame()
        frame_url = frame.url if frame is not None else p.url
        index = p.frames.index(frame) if frame in p.frames else 0
        first = descriptors[0]
        by_role = f'role={first.role or first.tag}[name="{first.name}"]'
        box = found.get("box")
        return SubmitTarget(
            element=first,
            ancestors=descriptors[1:],
            selectors=(*found.get("selectors", ()), by_role),
            frame_url=frame_url,
            page_url=p.url,
            frame_index=index,
            box=TargetBox.model_validate(box) if box else None,
        )
