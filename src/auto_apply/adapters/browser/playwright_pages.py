"""PageDriver — Playwright 로 PlaywrightBrowserHost 의 탭을 조작한다 (§A5).

ref 는 snapshot 때 쥔 요소 핸들의 이름표다. 표는 탭마다 하나고 snapshot·이동 때 통째로 버린다.
"""

import asyncio
import contextlib
from collections.abc import Awaitable
from itertools import count
from typing import Any

from playwright.async_api import ElementHandle, FilePayload, Frame, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.adapters.browser.snapshot_script import (
    COLLECT,
    DESCRIBE,
    FIND_OPTION,
    MAX_NODES,
    SCROLL_PAGE,
    SELECTED_LABELS,
)
from auto_apply.contracts.page import PageSnapshot, SnapshotNode, UploadFile
from auto_apply.domain import page_elements as rules
from auto_apply.domain.errors import PageActionFailed, PageFailure
from auto_apply.ports.browser import PageHandle

_ACTION_TIMEOUT_MS = 5_000
_NAVIGATION_TIMEOUT_MS = 30_000
_POLL_S = 0.2


class PlaywrightPageDriver:
    def __init__(self, host: PlaywrightBrowserHost) -> None:
        self._host = host
        self._refs: dict[str, dict[str, ElementHandle]] = {}
        # 탭·재기동을 넘어 ref 번호를 되풀이하지 않는다 — 옛 ref 가 새 요소를 가리킬 일이 없다.
        self._ids = count(1)

    def _page(self, handle: PageHandle) -> Page:
        try:
            return self._host.vendor_page(handle)
        except LookupError as e:
            self._refs.pop(handle.id, None)
            raise PageActionFailed(PageFailure.STALE_REF, "탭이 닫혔다 — snapshot 부터") from e

    async def snapshot(self, page: PageHandle) -> PageSnapshot:
        p = self._page(page)
        await self._forget(page)
        table: dict[str, ElementHandle] = {}
        nodes: list[SnapshotNode] = []
        frames: list[str] = []
        truncated = False
        for frame in p.frames:
            if frame.is_detached() or len(nodes) >= MAX_NODES:
                truncated = truncated or len(nodes) >= MAX_NODES
                continue
            try:
                got = await self._collect(frame, len(frames), table, MAX_NODES - len(nodes))
            except PlaywrightError:
                continue  # 수집 중 사라진 프레임
            frames.append(frame.url)
            nodes.extend(got[0])
            truncated = truncated or got[1]
        self._refs[page.id] = table
        return PageSnapshot(
            url=p.url, title=await p.title(), frames=tuple(frames), nodes=tuple(nodes),
            truncated=truncated,
        )  # fmt: skip

    async def _collect(
        self, frame: Frame, index: int, table: dict[str, ElementHandle], limit: int
    ) -> tuple[list[SnapshotNode], bool]:
        result = await frame.evaluate_handle(COLLECT, limit)
        try:
            infos: list[dict[str, Any]] = await (await result.get_property("infos")).json_value()
            truncated = bool(await (await result.get_property("truncated")).json_value())
            elements = await (await result.get_property("els")).get_properties()
        finally:
            await result.dispose()
        nodes: list[SnapshotNode] = []
        for i, info in enumerate(infos):
            element = elements[str(i)].as_element()
            ref = None
            if element is not None:
                ref = f"e{next(self._ids)}"
                table[ref] = element
            nodes.append(SnapshotNode.model_validate({**info, "ref": ref, "frame": index}))
        return nodes, truncated

    async def _forget(self, page: PageHandle) -> None:
        for element in self._refs.pop(page.id, {}).values():
            with contextlib.suppress(PlaywrightError):
                await element.dispose()

    async def _element(self, page: PageHandle, ref: str) -> tuple[ElementHandle, dict[str, Any]]:
        self._page(page)
        element = self._refs.get(page.id, {}).get(ref)
        if element is None:
            raise PageActionFailed(
                PageFailure.STALE_REF, f"{ref} 는 최신 snapshot 의 ref 가 아니다"
            )
        try:
            desc: dict[str, Any] = await element.evaluate(DESCRIBE)
        except PlaywrightError as e:
            raise PageActionFailed(PageFailure.STALE_REF, f"{ref} 가 페이지에서 사라졌다") from e
        if not desc["connected"]:
            raise PageActionFailed(PageFailure.STALE_REF, f"{ref} 가 페이지에서 사라졌다")
        if rules.is_secret_field(desc["tag"], desc["type"], desc["autocomplete"]):
            raise PageActionFailed(
                PageFailure.SECRET_FIELD, "비밀번호·인증 코드 칸은 입력하지 않는다"
            )
        return element, desc

    async def navigate(self, page: PageHandle, url: str) -> None:
        p = self._page(page)
        await self._forget(page)
        try:
            await p.goto(url, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as e:
            raise PageActionFailed(PageFailure.NAVIGATION_FAILED, "페이지를 열지 못했다") from e

    async def back(self, page: PageHandle) -> None:
        p = self._page(page)
        await self._forget(page)
        before = p.url
        try:
            response = await p.go_back(
                wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS
            )
        except PlaywrightError as e:
            raise PageActionFailed(PageFailure.NAVIGATION_FAILED, "뒤로 가지 못했다") from e
        if response is None and p.url == before:
            raise PageActionFailed(PageFailure.NAVIGATION_FAILED, "뒤로 갈 페이지가 없다")

    async def scroll(self, page: PageHandle, ref: str | None, *, down: bool) -> None:
        if ref is None:
            await self._page(page).evaluate(SCROLL_PAGE, down)
            return
        element, _ = await self._element(page, ref)
        await _act(element.scroll_into_view_if_needed(timeout=_ACTION_TIMEOUT_MS))

    async def wait_for_text(self, page: PageHandle, text: str, *, timeout_ms: int) -> bool:
        p = self._page(page)
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            for frame in p.frames:
                with contextlib.suppress(PlaywrightError):
                    if await frame.get_by_text(text).first.is_visible():
                        return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(_POLL_S)

    async def fill(self, page: PageHandle, ref: str, value: str) -> None:
        element, desc = await self._element(page, ref)
        if not rules.accepts_text(desc["tag"], desc["type"], contenteditable=desc["editable"]):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 글자 칸이 아니다")
        # fill 은 값을 넣고 input·change 이벤트만 낸다 — 키 입력이 아니라 Enter 암묵 제출이 없다.
        await _act(element.fill(value, timeout=_ACTION_TIMEOUT_MS))

    async def select(self, page: PageHandle, ref: str, option: str) -> str:
        element, desc = await self._element(page, ref)
        if not rules.is_native_select(desc["tag"]):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 select 가 아니다")
        index: int = await element.evaluate(FIND_OPTION, option)
        if index < 0:
            raise PageActionFailed(PageFailure.OPTION_NOT_FOUND, f"{ref} 에 그런 선택지가 없다")
        await _act(element.select_option(index=index, timeout=_ACTION_TIMEOUT_MS))
        chosen: str = await element.evaluate(SELECTED_LABELS)
        return chosen

    async def set_checked(self, page: PageHandle, ref: str, on: bool) -> None:
        element, desc = await self._element(page, ref)
        if not rules.is_toggle(desc["tag"], desc["type"]) or (
            not on and rules.is_radio(desc["tag"], desc["type"])
        ):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 켜고 끌 수 없다")
        await _act(element.set_checked(on, timeout=_ACTION_TIMEOUT_MS))

    async def upload(self, page: PageHandle, ref: str, file: UploadFile) -> None:
        element, desc = await self._element(page, ref)
        if not rules.is_file_input(desc["tag"], desc["type"]):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 파일 입력이 아니다")
        payload = FilePayload(name=file.name, mimeType=file.content_type, buffer=file.data)
        await _act(element.set_input_files(payload, timeout=_ACTION_TIMEOUT_MS))


async def _act[T](action: Awaitable[T]) -> T:
    try:
        return await action
    except PlaywrightTimeout as e:
        raise PageActionFailed(PageFailure.TIMEOUT, "요소가 조작할 수 있는 상태가 아니다") from e
    except PlaywrightError as e:
        raise PageActionFailed(
            PageFailure.STALE_REF, "요소를 조작하지 못했다 — snapshot 부터"
        ) from e
