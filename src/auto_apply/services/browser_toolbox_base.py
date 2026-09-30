"""BrowserToolbox 의 상태와 하네스 창 실행 (§A5, §A4).

도구 처리기는 browser_toolbox*.py 가 나눠 갖는다.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

import structlog

from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.fill_log import FillAction, FillLog, FillSource
from auto_apply.contracts.knowledge import DocumentMeta
from auto_apply.contracts.page import PageSnapshot, SnapshotNode
from auto_apply.contracts.submit_guard import GuardReport, ReviewRecord
from auto_apply.domain.errors import PageActionFailed, SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import GuardMode, carried_values
from auto_apply.ports.browser import BrowserHost, GuardedPageDriver, PageHandle
from auto_apply.services.browser_toolbox_record import (
    blocked_result,
    fail,
    fill_entry,
    incident_result,
)
from auto_apply.services.submit_guard import SubmitGuard

log = structlog.get_logger("auto_apply.services.browser_toolbox")


class DocumentReader(Protocol):
    """앱이 관리하는 문서만 읽는 통로 (UploadService 가 만족한다). 없거나 남의 것이면 NotFound."""

    async def read_document(self, user_id: str, document_id: str) -> tuple[DocumentMeta, bytes]: ...


class Refused(Exception):
    def __init__(self, error: ToolError, message: str) -> None:
        super().__init__(message)
        self.error = error


class ToolboxBase:
    def __init__(
        self,
        host: BrowserHost,
        pages: GuardedPageDriver,
        documents: DocumentReader,
        *,
        user_id: str,
        application_id: str | None,
        run_id: str | None,
        forbidden_origins: Iterable[str] = (),
        step: int = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._host, self._pages, self._documents = host, pages, documents
        self._user_id, self._step = user_id, step
        self._log = log.bind(application_id=application_id, run_id=run_id)
        self._forbidden = tuple(forbidden_origins)
        self._guard = SubmitGuard(pages, forbidden_origins=self._forbidden, sleep=sleep)
        self._armed: PageHandle | None = None
        self._sleep = sleep
        self._snapshot: PageSnapshot | None = None
        self._fill_log = FillLog()
        self._failure: str | None = None
        self._incident: str | None = None
        self._review: ReviewRecord | None = None

    @property
    def fill_log(self) -> FillLog:
        return self._fill_log

    @property
    def failure(self) -> str | None:
        """report_failure 의 이유. 부른 적이 없으면 None."""
        return self._failure

    @property
    def incident(self) -> str | None:
        """L5 사후 감지 근거 — 있으면 제출이 뚫렸을 수 있다. run 은 멈췄다 (§A4 L5)."""
        return self._incident

    @property
    def review(self) -> ReviewRecord | None:
        """ready_for_review 로 확정한 기록. 부른 적이 없으면 None."""
        return self._review

    async def close(self) -> None:
        """run 이 끝났다 — 가드를 내려 사람이 브라우저를 쓸 수 있게 한다(앱 출처 차단은 남는다).

        부르지 않으면 가드는 켜진 채 남는다(닫힌 쪽).
        """
        page, self._armed = self._armed, None
        if page is not None and self._host.running:
            with contextlib.suppress(PageActionFailed, SubmitGuardUnavailable):
                await self._guard.disarm(page)

    # ------------------------------------------------------------------ 하네스
    async def _page(self) -> PageHandle:
        page = await self._host.page()
        await self._guard.arm(page)  # 페이지를 건드리는 모든 도구의 전제 — 못 켜면 예외
        self._armed = page
        return page

    async def _guarded(
        self,
        tool: str,
        page: PageHandle,
        mode: GuardMode,
        action: Callable[[], Awaitable[Any]],
        done: Callable[[Any], ToolResult] | None = None,
    ) -> ToolResult:
        """`action` 을 하네스 창 안에서. 성공하면 `done(값)` 이 결과를 만든다(FillLog 기록 포함)."""
        out = await self._guard.run(page, mode, action, carried=self._typed_values())
        if out.evidence is not None:
            return self._stop(tool, out.evidence, out.report)
        result = ToolResult(tool=tool, ok=True)
        if out.error is None and done is not None:
            result = done(out.value)
        if out.blocked_in_window:
            # 입력은 됐어도(FillLog 기록) 사이트가 제출을 시도했다 —
            # 에이전트가 놓치지 않게 실패로 알린다.
            self._log.info("submit_guard.blocked", tool=tool, mode=mode.value)
            return blocked_result(tool, out.report)
        if out.error is not None:
            return fail(tool, ToolError(out.error.reason.value), str(out.error), out.report)
        return result.model_copy(update={"guard": out.report})

    def _stop(self, tool: str, evidence: str, report: GuardReport) -> ToolResult:
        self._incident = evidence
        self._log.error("submit_guard.incident", tool=tool, evidence=evidence)
        return incident_result(tool, report)

    def _typed_values(self) -> tuple[str, ...]:
        entries = self._fill_log.entries
        return carried_values(e.value for e in entries if e.action is FillAction.FILL and e.value)

    # ------------------------------------------------------------------ snapshot·FillLog
    def _node(self, ref: str) -> SnapshotNode:
        node = None if self._snapshot is None else self._snapshot.node(ref)
        if node is None:
            raise Refused(ToolError.STALE_REF, f"{ref} 는 최신 snapshot 의 ref 가 아니다")
        return node

    def _input_node(self, ref: str) -> SnapshotNode:
        node = self._node(ref)
        if node.secret:  # 드라이버도 그 순간의 DOM 으로 다시 본다 (절대 규칙 3)
            raise Refused(ToolError.SECRET_FIELD, "비밀번호·인증 코드 칸은 입력하지 않는다")
        return node

    def _record(
        self, action: FillAction, node: SnapshotNode, source: FillSource | None, **what: Any
    ) -> None:
        assert self._snapshot is not None
        entry = fill_entry(
            self._fill_log.next_seq, self._step, action, self._snapshot, node, source, **what
        )
        self._fill_log = self._fill_log.append(entry)
