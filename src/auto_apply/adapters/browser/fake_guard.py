"""GuardedPageDriver 테스트 대역 — 메모리 DOM 위의 하네스 (§A4).

실제 구현(playwright_guarded)과 같은 결정 규칙(domain/submit_guard_policy)을 쓴다. 요소의 핸들러는
`fake_effects` 로 적고, 하네스를 통과한 요청만 `sent` 에 남는다 — 테스트는 "제출 0건"을 여기서 본다.
"""

import copy
from collections.abc import Mapping
from urllib.parse import urlsplit

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_effects import (
    ChooseFile,
    Dialog,
    Effect,
    Later,
    Send,
    Show,
    SubmitForm,
)
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement, FakePageDriver
from auto_apply.contracts.click import ElementDescriptor
from auto_apply.contracts.page import UploadFile
from auto_apply.contracts.submit_guard import (
    BlockedAction,
    DialogEvent,
    GuardReport,
    PageText,
    SubmitTarget,
)
from auto_apply.domain import page_elements as rules
from auto_apply.domain.errors import PageActionFailed, PageFailure, SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import (
    BlockReason,
    GuardMode,
    dialog_verdict,
    request_verdict,
)
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers
from auto_apply.ports.browser import PageHandle

_TEXT_ROLES = frozenset({"text", "heading", "status", "alert", "button", "link"})


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""


class FakeGuardedPageDriver(FakePageDriver):
    def __init__(self, host: FakeBrowserHost, sites: Mapping[str, FakeDocument]) -> None:
        super().__init__(host, sites)
        self.armed = False
        self.mode = GuardMode.STRICT
        self.fail_arm = False  # 테스트: 하네스 설치 실패 흉내
        self.sent: list[tuple[str, str]] = []  # 하네스를 통과해 "서버"에 닿은 요청
        # 가드가 꺼진 동안 뜬 대화상자·파일 선택 창 — 사람에게 둔다(처리하지 않는다)
        self.left_to_human: list[str] = []
        self._carried: tuple[str, ...] = ()
        self._forbidden: tuple[str, ...] = ()
        self._blocked: list[BlockedAction] = []
        self._dialogs: list[DialogEvent] = []
        self._later: list[Effect] = []
        self._due: list[Effect] = []

    # ------------------------------------------------------------------ 하네스
    async def arm(self, page: PageHandle, *, forbidden_origins: tuple[str, ...]) -> None:
        self._tab(page)
        if self.fail_arm:
            raise SubmitGuardUnavailable("하네스를 켜지 못했다")
        if not self.armed:
            self.armed, self.mode = True, GuardMode.STRICT
        self._forbidden = tuple(dict.fromkeys((*self._forbidden, *forbidden_origins)))

    async def disarm(self, page: PageHandle) -> None:
        self.armed = False

    async def set_window(
        self, page: PageHandle, mode: GuardMode, *, carried: tuple[str, ...]
    ) -> None:
        if not self.armed:
            raise SubmitGuardUnavailable("가드가 켜져 있지 않다")
        self.mode, self._carried = mode, carried

    async def settle(self, page: PageHandle) -> None:
        return None

    async def drain(self, page: PageHandle) -> GuardReport:
        # 타이머는 창을 닫는 drain 을 지나 **다음** drain(창 사이) 때 돈다 — 그때의(유지된) 모드로
        due, self._due, self._later = self._due, self._later, []
        for effect in due:
            self._fire(page, effect)
        report = GuardReport(blocked=tuple(self._blocked), dialogs=tuple(self._dialogs))
        self._blocked, self._dialogs = [], []
        return report

    # ------------------------------------------------------------------ 요소
    async def describe(self, page: PageHandle, ref: str) -> tuple[ElementDescriptor, ...]:
        el = self._element(page, ref)
        return (_descriptor(el), *el.ancestors)

    async def click(self, page: PageHandle, ref: str) -> None:
        if not self.armed:  # 하네스 없는 클릭은 없다
            raise SubmitGuardUnavailable("가드가 켜져 있지 않다")
        el = self._element(page, ref)
        if rules.is_file_input(el.tag, el.type):
            raise PageActionFailed(PageFailure.UNSUPPORTED_ELEMENT, "파일 입력은 upload 로 넣는다")
        self._fire_all(page, el.on_click)

    async def fill(self, page: PageHandle, ref: str, value: str) -> None:
        await super().fill(page, ref, value)
        self._fire_all(page, self._element(page, ref).on_change)

    async def select(self, page: PageHandle, ref: str, option: str) -> str:
        chosen = await super().select(page, ref, option)
        self._fire_all(page, self._element(page, ref).on_change)
        return chosen

    async def set_checked(self, page: PageHandle, ref: str, on: bool) -> None:
        await super().set_checked(page, ref, on)
        self._fire_all(page, self._element(page, ref).on_change)

    async def upload(self, page: PageHandle, ref: str, file: UploadFile) -> None:
        await super().upload(page, ref, file)
        self._fire_all(page, self._element(page, ref).on_change)

    async def read_text(self, page: PageHandle) -> PageText:
        tab = self._tab(page)
        doc = tab.doc or FakeDocument()
        lines = [e.name for e in doc.elements if e.visible and e.name and e.role in _TEXT_ROLES]
        return PageText(urls=(tab.history[-1], *doc.frames), lines=tuple(lines))

    async def submit_target(self, page: PageHandle, ref: str) -> SubmitTarget:
        el = self._element(page, ref)
        tab = self._tab(page)
        frames = (tab.history[-1], *(tab.doc.frames if tab.doc else ()))
        return SubmitTarget(
            element=_descriptor(el),
            ancestors=el.ancestors,
            selectors=(f'role={el.role}[name="{el.name}"]',),
            frame_url=frames[el.frame] if el.frame < len(frames) else frames[0],
            page_url=tab.history[-1],
            frame_index=el.frame,
        )

    def human_click(self, page: PageHandle, name: str) -> None:
        """사람이 하네스를 거치지 않고 `name` 버튼을 누른다(테스트: 핸드오프 중의 사람)."""
        doc = self.document(page)
        el = next(e for e in doc.elements if e.role == "button" and e.name == name)
        self._fire_all(page, el.on_click)

    # ------------------------------------------------------------------ 핸들러 실행
    def _fire_all(self, page: PageHandle, effects: tuple[Effect, ...]) -> None:
        for effect in effects:
            self._fire(page, effect)

    def _fire(self, page: PageHandle, effect: Effect) -> None:
        if isinstance(effect, Later):
            self._later.extend(effect.effects)
        elif isinstance(effect, Show):
            self.document(page).elements.append(FakeElement(effect.role, effect.text, tag="p"))
        elif isinstance(effect, Dialog | ChooseFile) and not self.armed:
            self.left_to_human.append(effect.kind if isinstance(effect, Dialog) else "filechooser")
        elif isinstance(effect, ChooseFile):
            self._dialogs.append(DialogEvent(kind="filechooser", accepted=False))
        elif isinstance(effect, Dialog):
            accepted = dialog_verdict(effect.kind) == "accept"
            message = redact_resident_registration_numbers(effect.message)[0][:200]
            self._dialogs.append(DialogEvent(kind=effect.kind, message=message, accepted=accepted))
            if accepted:
                self._fire_all(page, effect.then)
        elif isinstance(effect, SubmitForm):
            if self.armed:
                self._blocked.append(
                    BlockedAction(reason=BlockReason.FORM_SUBMIT, resource="submit")
                )
            else:
                self._fire(page, Send(effect.method, effect.url, navigation=True))
        else:
            self._send(page, effect)

    def _send(self, page: PageHandle, req: Send) -> None:
        reason = request_verdict(
            mode=self.mode if self.armed else None, method=req.method, url=req.url,
            navigation=req.navigation, carried=self._carried, forbidden_origins=self._forbidden,
        )  # fmt: skip
        if reason is not None:
            resource = "document" if req.navigation else "fetch"
            blocked = BlockedAction(
                reason=reason, method=req.method, resource=resource, origin=_origin(req.url)
            )
            self._blocked.append(blocked)
            return
        self.sent.append((req.method, req.url))
        if req.navigation:
            tab = self._tab(page)
            tab.refs = {}
            tab.history.append(req.url)
            known = self._sites.get(req.url)
            tab.doc = copy.deepcopy(known) if known is not None else FakeDocument()
            self._host.fake_page(page).url = req.url


def _descriptor(el: FakeElement) -> ElementDescriptor:
    return ElementDescriptor(
        tag=el.tag, type=el.type, role=el.role, name=el.name, text=el.name, in_form=el.in_form,
        is_form_default_button=el.default_button, in_dialog=el.in_dialog, href=el.href,
    )  # fmt: skip
