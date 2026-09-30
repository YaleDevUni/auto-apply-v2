"""브라우저 context 하나의 제출 차단 장치 — route(L3 네트워크)·init script(L3 페이지)·대화상자(L4).

route·대화상자 처리기는 처음 켤 때 달고 떼지 않는다(앱 출처 차단은 꺼져도 남는다; 대화상자는 꺼져
있으면 사람에게 둔다). 페이지 스크립트·파일 선택 창 가로채기는 켤 때 걸고 끌 때 걷는다. 다시 켤 때
프레임마다 가드가 우리 것인지 확인한다(VERIFY) — 못 하면 켜지 않은 것이다. 근거는 §A4 켜고 끄기.
"""

import asyncio
import contextlib
import secrets
from typing import Protocol
from urllib.parse import urlsplit

import structlog
from playwright.async_api import BrowserContext, Dialog, FileChooser, Frame, Page, Request, Route
from playwright.async_api import Error as PlaywrightError

from auto_apply.adapters.browser.guard_script import CONTROL, GUARD_KEY, VERIFY, guard_script
from auto_apply.contracts.submit_guard import BlockedAction, DialogEvent, GuardReport
from auto_apply.domain.errors import SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import (
    BlockReason,
    GuardMode,
    dialog_verdict,
    request_verdict,
)
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers

log = structlog.get_logger(__name__)


class _Closable(Protocol):
    """add_init_script 가 돌려주는 등록 핸들 — close() 가 등록을 걷는다."""

    async def close(self) -> None: ...


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else parts.scheme


class ContextGuard:
    def __init__(self, context: BrowserContext, token: str) -> None:
        self._context = context
        self._token = token
        self._installed = False
        self._script: _Closable | None = None
        self.armed = False  # route 가 run 규칙으로 막는다
        self.ready = False  # 페이지 스크립트 층까지 섰다
        self.mode = GuardMode.STRICT
        self.carried: tuple[str, ...] = ()
        self.forbidden: tuple[str, ...] = ()
        self._blocked: list[BlockedAction] = []
        self._dialogs: list[DialogEvent] = []
        # 진행 중인 요청과 시작 시각 — settle 은 이번 창에서 시작된 것만 기다린다
        self.inflight: dict[Request, float] = {}
        self.window_started = 0.0

    # ------------------------------------------------------------------ 켜기·끄기
    async def arm(self, forbidden_origins: tuple[str, ...]) -> None:
        self.forbidden = tuple(dict.fromkeys((*self.forbidden, *forbidden_origins)))
        if not self._installed:
            await self._context.route("**/*", self._route)
            self._context.on("dialog", self._on_dialog)
            self._context.on("page", self._on_page)
            # Playwright 는 내장 함수(set.add)를 처리기로 받지 못한다 — 메서드로 감싼다
            self._context.on("request", self._started)
            self._context.on("requestfinished", self._ended)
            self._context.on("requestfailed", self._ended)
            self._installed = True
        if self.ready:
            return
        if not self.armed:
            # 막 켠 가드는 닫힌 쪽(strict)에서 시작한다. route 는 지금부터 막는다 — 아래 스크립트 층
            # 설치가 실패해도 네트워크 층은 켜져 있다.
            self.mode = GuardMode.STRICT
            self.armed = True
        for page in self._context.pages:  # 에이전트의 클릭이 OS 파일 선택 창을 띄우지 않게
            self._watch_file_chooser(page, on=True)
        if self._script is None:
            self._script = await self._context.add_init_script(script=guard_script(self._token))
        for frame in self._frames():  # 이미 열린 문서에는 init script 가 없다
            await self._run(frame, guard_script(self._token))
            decoy = secrets.token_hex(16)
            if await self._run(frame, VERIFY, [GUARD_KEY, self._token, decoy]) is False:
                raise SubmitGuardUnavailable("페이지가 제출 차단 스크립트 자리를 먼저 차지했다")
        self.ready = True  # 두 층이 다 섰다 — 그 전에는 창을 열지 않는다(드라이버가 확인)

    async def disarm(self) -> None:
        self.armed = self.ready = False
        script, self._script = self._script, None
        if script is not None:
            with contextlib.suppress(PlaywrightError):
                await script.close()  # init script 등록을 걷는다 — 이후 문서에는 심지 않는다
        for page in self._context.pages:
            self._watch_file_chooser(page, on=False)
        for frame in self._frames():
            with contextlib.suppress(PlaywrightError):
                await frame.evaluate(CONTROL, [GUARD_KEY, self._token, False])

    async def drain(self) -> GuardReport:
        scripted: list[BlockedAction] = []
        for frame in self._frames():
            with contextlib.suppress(PlaywrightError):
                events = await frame.evaluate(CONTROL, [GUARD_KEY, self._token, None])
                for what in events or ():
                    scripted.append(
                        BlockedAction(reason=BlockReason.FORM_SUBMIT, resource=str(what)[:40])
                    )
        report = GuardReport(blocked=(*self._blocked, *scripted), dialogs=tuple(self._dialogs))
        self._blocked, self._dialogs = [], []
        return report

    # ------------------------------------------------------------------ 처리기
    async def _route(self, route: Route, request: Request) -> None:
        try:
            reason = request_verdict(
                mode=self.mode if self.armed else None, method=request.method, url=request.url,
                navigation=request.is_navigation_request(), carried=self.carried,
                forbidden_origins=self.forbidden,
            )  # fmt: skip
        except Exception:  # 판정이 실패하면 막는다 (닫힌 쪽)
            log.exception("submit_guard.verdict_failed", application_id=None, run_id=None)
            reason = BlockReason.GUARD_ERROR
        if reason is None:
            with contextlib.suppress(PlaywrightError):
                await route.continue_()
            return
        try:
            self._blocked.append(
                BlockedAction(
                    reason=reason, method=request.method, resource=request.resource_type,
                    origin=_origin(request.url),
                )
            )  # fmt: skip
        except Exception:  # 기록이 실패해도 막는 것은 막는다 — 막았다는 사실만 남긴다
            log.exception("submit_guard.record_failed", application_id=None, run_id=None)
            self._blocked.append(BlockedAction(reason=reason))
        finally:
            # 문서 탐색은 aborted 로 끊어야 오류 페이지로 바뀌지 않고 채우던 화면이 남는다.
            with contextlib.suppress(PlaywrightError):
                await route.abort(
                    "aborted" if request.is_navigation_request() else "blockedbyclient"
                )

    async def _on_dialog(self, dialog: Dialog) -> None:
        if not self.armed:
            return  # 사람이 브라우저를 쓰는 동안(핸드오프·run 밖) — 사람이 고른다
        accept = dialog_verdict(dialog.type) == "accept"
        message = redact_resident_registration_numbers(dialog.message)[0][:200]
        self._dialogs.append(DialogEvent(kind=dialog.type, message=message, accepted=accept))
        with contextlib.suppress(PlaywrightError):
            await (dialog.accept() if accept else dialog.dismiss())

    def _on_file_chooser(self, _: FileChooser) -> None:
        # 파일을 넣지 않고 흘려보낸다 — 파일은 upload 도구로 앱이 관리하는 문서만 (§A5)
        self._dialogs.append(DialogEvent(kind="filechooser", accepted=False))

    def _on_page(self, page: Page) -> None:
        if self.armed:
            self._watch_file_chooser(page, on=True)

    def _watch_file_chooser(self, page: Page, *, on: bool) -> None:
        # 처리기가 있는 동안만 Playwright 가 OS 창을 가로챈다. 두 번 달지 않게 먼저 뗀다.
        with contextlib.suppress(KeyError, ValueError):
            page.remove_listener("filechooser", self._on_file_chooser)
        if on:
            page.on("filechooser", self._on_file_chooser)

    # ------------------------------------------------------------------ 내부
    def _started(self, request: Request) -> None:
        self.inflight[request] = asyncio.get_running_loop().time()

    def _ended(self, request: Request) -> None:
        self.inflight.pop(request, None)

    def pending_in_window(self) -> bool:
        """이번 창에서 시작돼 아직 안 끝난 요청이 있나.

        닫힌 탭의 요청은 끝 이벤트가 오지 않으므로 버린다.
        """
        for request, started in list(self.inflight.items()):
            try:
                closed = request.frame.page.is_closed()
            except PlaywrightError:  # 프레임이 사라졌다(서비스 워커 요청 등)
                closed = True
            if closed:
                self.inflight.pop(request, None)
            elif started >= self.window_started:
                return True
        return False

    def _frames(self) -> list[Frame]:
        return [f for p in self._context.pages if not p.is_closed() for f in p.frames]

    async def _run(self, frame: Frame, script: str, arg: object = None) -> object:
        """떨어져 나간 프레임이면 None. 살아 있는 문서에 못 돌렸으면 예외 — 호출자가 닫는다."""
        try:
            return await frame.evaluate(script, arg)
        except PlaywrightError:
            if not frame.is_detached():
                raise
            return None
