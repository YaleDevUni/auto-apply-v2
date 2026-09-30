"""PageDriver 테스트 대역 — 메모리 DOM (§A5).

실제 구현과 같은 요소 규칙(domain/page_elements)을 쓴다.

사이트는 URL → `FakeDocument` 로 등록한다. 등록 안 된 URL 로 이동하면 NAVIGATION_FAILED.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import count

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.contracts.page import PageSnapshot, SnapshotNode, UploadFile
from auto_apply.domain import page_elements as rules
from auto_apply.domain.errors import PageActionFailed, PageFailure
from auto_apply.ports.browser import PageHandle

_CONTEXT_ROLES = frozenset({"text", "heading", "status", "alert", "dialog", "alertdialog"})


@dataclass
class FakeElement:
    role: str
    name: str = ""
    tag: str = "input"
    type: str | None = None
    autocomplete: str | None = None
    value: str = ""
    checked: bool = False
    options: tuple[str, ...] = ()  # 선택지 라벨 (value 도 같다고 본다)
    group: str | None = None  # radio name
    visible: bool = True
    frame: int = 0
    level: int | None = None
    file: UploadFile | None = None


@dataclass
class FakeDocument:
    title: str = ""
    elements: list[FakeElement] = field(default_factory=list)
    frames: tuple[str, ...] = ()  # 하위 프레임 URL (번호 1부터)


@dataclass
class _Tab:
    history: list[str] = field(default_factory=list)
    doc: FakeDocument | None = None
    refs: dict[str, FakeElement] = field(default_factory=dict)


class FakePageDriver:
    def __init__(self, host: FakeBrowserHost, sites: Mapping[str, FakeDocument]) -> None:
        self._host = host
        self._sites = sites
        self._tabs: dict[str, _Tab] = {}
        self._ids = count(1)

    def document(self, page: PageHandle) -> FakeDocument:
        """지금 탭의 DOM — 테스트가 페이지 스스로의 변화(칸 type 바꾸기 등)를 흉내 낼 때."""
        doc = self._tab(page).doc
        if doc is None:
            raise LookupError("열린 문서가 없다")
        return doc

    def _tab(self, page: PageHandle) -> _Tab:
        try:
            fake = self._host.fake_page(page)
        except LookupError as e:
            self._tabs.pop(page.id, None)
            raise PageActionFailed(PageFailure.STALE_REF, "탭이 닫혔다 — snapshot 부터") from e
        tab = self._tabs.setdefault(page.id, _Tab())
        if not tab.history:
            tab.history.append(fake.url)
        return tab

    async def snapshot(self, page: PageHandle) -> PageSnapshot:
        tab = self._tab(page)
        tab.refs = {}
        doc = tab.doc or FakeDocument()
        nodes = []
        for el in doc.elements:
            is_file = rules.is_file_input(el.tag, el.type)
            if not el.visible and not is_file:
                continue
            ref = None
            if el.role not in _CONTEXT_ROLES:
                ref = f"e{next(self._ids)}"
                tab.refs[ref] = el
            nodes.append(
                SnapshotNode.model_validate({**_info(el), "ref": ref, "hidden": not el.visible})
            )
        url = tab.history[-1]
        return PageSnapshot(url=url, title=doc.title, frames=(url, *doc.frames), nodes=tuple(nodes))

    async def navigate(self, page: PageHandle, url: str) -> None:
        tab = self._tab(page)
        tab.refs = {}
        if url not in self._sites:
            raise PageActionFailed(PageFailure.NAVIGATION_FAILED, "페이지를 열지 못했다")
        tab.history.append(url)
        tab.doc = copy.deepcopy(self._sites[url])
        self._host.fake_page(page).url = url

    async def back(self, page: PageHandle) -> None:
        tab = self._tab(page)
        tab.refs = {}
        if len(tab.history) < 2:
            raise PageActionFailed(PageFailure.NAVIGATION_FAILED, "뒤로 갈 페이지가 없다")
        tab.history.pop()
        url = tab.history[-1]
        tab.doc = copy.deepcopy(self._sites[url]) if url in self._sites else None
        self._host.fake_page(page).url = url

    async def scroll(self, page: PageHandle, ref: str | None, *, down: bool) -> None:
        if ref is not None:
            self._element(page, ref)

    async def wait_for_text(self, page: PageHandle, text: str, *, timeout_ms: int) -> bool:
        doc = self._tab(page).doc
        return doc is not None and any(e.visible and text in e.name for e in doc.elements)

    def _element(self, page: PageHandle, ref: str) -> FakeElement:
        tab = self._tab(page)
        el = tab.refs.get(ref)
        if el is None or tab.doc is None or not any(el is e for e in tab.doc.elements):
            raise PageActionFailed(
                PageFailure.STALE_REF, f"{ref} 는 최신 snapshot 의 ref 가 아니다"
            )
        if rules.is_secret_field(el.tag, el.type, el.autocomplete):
            raise PageActionFailed(
                PageFailure.SECRET_FIELD, "비밀번호·인증 코드 칸은 입력하지 않는다"
            )
        return el

    async def fill(self, page: PageHandle, ref: str, value: str) -> None:
        el = self._element(page, ref)
        if not rules.accepts_text(el.tag, el.type):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 글자 칸이 아니다")
        el.value = value

    async def select(self, page: PageHandle, ref: str, option: str) -> str:
        el = self._element(page, ref)
        if not rules.is_native_select(el.tag):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 select 가 아니다")
        if option.strip() not in el.options:
            raise PageActionFailed(PageFailure.OPTION_NOT_FOUND, f"{ref} 에 그런 선택지가 없다")
        el.value = option.strip()
        return el.value

    async def set_checked(self, page: PageHandle, ref: str, on: bool) -> None:
        el = self._element(page, ref)
        if not rules.is_toggle(el.tag, el.type) or (not on and rules.is_radio(el.tag, el.type)):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 켜고 끌 수 없다")
        if on and rules.is_radio(el.tag, el.type):
            for other in self.document(page).elements:
                if other.group is not None and other.group == el.group:
                    other.checked = False
        el.checked = on

    async def upload(self, page: PageHandle, ref: str, file: UploadFile) -> None:
        el = self._element(page, ref)
        if not rules.is_file_input(el.tag, el.type):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, f"{ref} 는 파일 입력이 아니다")
        el.file = file


def _info(el: FakeElement) -> dict[str, object]:
    info: dict[str, object] = {
        "role": el.role,
        "name": el.name,
        "tag": el.tag,
        "input_type": el.type,
        "autocomplete": el.autocomplete,
        "frame": el.frame,
        "level": el.level,
    }
    if rules.is_toggle(el.tag, el.type):
        info["checked"] = el.checked
    elif rules.is_native_select(el.tag):
        info["options"] = el.options
        info["value"] = el.value
    elif rules.accepts_text(el.tag, el.type) and not rules.is_secret_field(
        el.tag, el.type, el.autocomplete
    ):
        info["value"] = el.value
    return info
