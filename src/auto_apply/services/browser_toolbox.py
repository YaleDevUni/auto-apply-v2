"""BrowserToolbox — 에이전트 도구의 실행 (§A5). 도구 정의는 browser_toolbox_specs.TOOLS 하나다.

LLM 이 만든 인자는 `call()` 에서 Pydantic 검증을 통과해야만 브라우저에 닿는다(절대 규칙 4).
fill·select·check·upload 가 성공하면 FillLog 에 근거와 함께 남긴다 — 에이전트는 기록을 직접
쓰지 못한다.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ValidationError

from auto_apply.contracts.browser_tools import (
    CheckInput,
    FillInput,
    NavigateInput,
    ReportFailureInput,
    ScrollInput,
    SelectInput,
    ToolError,
    ToolResult,
    UploadInput,
    WaitForInput,
)
from auto_apply.contracts.fill_log import FillAction, FillLog, FillSource
from auto_apply.contracts.knowledge import DocumentMeta
from auto_apply.contracts.page import PageSnapshot, SnapshotNode, UploadFile
from auto_apply.domain.errors import NotFound, PageActionFailed
from auto_apply.domain.url_policy import is_forbidden_url
from auto_apply.ports.browser import BrowserHost, PageDriver
from auto_apply.services.browser_toolbox_record import describe_validation_error, fill_entry
from auto_apply.services.browser_toolbox_redact import redact_snapshot, redact_text
from auto_apply.services.browser_toolbox_specs import TOOLS

log = structlog.get_logger(__name__)
WAIT_TEXT_TIMEOUT_MS = 10_000


class DocumentReader(Protocol):
    """앱이 관리하는 문서만 읽는 통로 (UploadService 가 만족한다). 없거나 남의 것이면 NotFound."""

    async def read_document(self, user_id: str, document_id: str) -> tuple[DocumentMeta, bytes]: ...


class _Refused(Exception):
    def __init__(self, error: ToolError, message: str) -> None:
        super().__init__(message)
        self.error = error


class BrowserToolbox:
    """run 하나의 도구 상자. 탭은 BrowserHost 의 작업 탭 하나다(새 탭·팝업 조작은 없다)."""

    def __init__(
        self,
        host: BrowserHost,
        pages: PageDriver,
        documents: DocumentReader,
        *,
        user_id: str,
        application_id: str | None,
        run_id: str | None,
        forbidden_origins: Iterable[str] = (),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._host, self._pages, self._documents = host, pages, documents
        self._user_id = user_id
        self._log = log.bind(application_id=application_id, run_id=run_id)
        self._forbidden = tuple(forbidden_origins)
        self._sleep = sleep
        self._snapshot: PageSnapshot | None = None
        self._fill_log = FillLog()
        self._failure: str | None = None
        self._handlers: dict[str, Callable[[Any], Awaitable[ToolResult]]] = {
            "snapshot": self._do_snapshot,
            "navigate": self._navigate,
            "back": self._back,
            "scroll": self._scroll,
            "wait_for": self._wait_for,
            "fill": self._fill,
            "select": self._select,
            "check": self._check,
            "upload": self._upload,
            "report_failure": self._report_failure,
        }
        assert self._handlers.keys() == TOOLS.keys()  # 정의와 실행이 어긋나면 기동부터 실패

    @property
    def fill_log(self) -> FillLog:
        return self._fill_log

    @property
    def failure(self) -> str | None:
        """report_failure 의 이유. 부른 적이 없으면 None."""
        return self._failure

    async def call(self, tool: str, args: Mapping[str, object] | None = None) -> ToolResult:
        """에이전트 도구 호출의 유일한 입구."""
        result = await self._call(tool, args or {})
        self._log.info("toolbox.call", tool=result.tool, ok=result.ok, error=result.error)
        return result

    async def _call(self, tool: str, args: Mapping[str, object]) -> ToolResult:
        spec = TOOLS.get(tool)
        if spec is None:
            # 이름도 LLM 이 만든 문자열이다 — 결과·로그에 그대로 되돌리지 않는다.
            return _fail("unknown", ToolError.UNKNOWN_TOOL, "없는 도구")
        if self._failure is not None:
            return _fail(tool, ToolError.RUN_FINISHED, "report_failure 로 끝난 run 이다")
        try:
            data = spec.input_model.model_validate(dict(args))
        except ValidationError as e:
            return _fail(tool, ToolError.INVALID_INPUT, describe_validation_error(e))
        try:
            return await self._handlers[tool](data)
        except _Refused as r:
            return _fail(tool, r.error, str(r))
        except PageActionFailed as e:
            return _fail(tool, ToolError(e.reason.value), str(e))

    # ------------------------------------------------------------------ 이동
    async def _do_snapshot(self, _: BaseModel) -> ToolResult:
        self._snapshot = redact_snapshot(await self._pages.snapshot(await self._host.page()))
        return ToolResult(tool="snapshot", ok=True, snapshot=self._snapshot)

    async def _navigate(self, data: NavigateInput) -> ToolResult:
        if is_forbidden_url(data.url, self._forbidden):
            raise _Refused(ToolError.FORBIDDEN_URL, "열 수 없는 주소다")
        self._snapshot = None
        await self._pages.navigate(await self._host.page(), data.url)
        return ToolResult(tool="navigate", ok=True)

    async def _back(self, _: BaseModel) -> ToolResult:
        self._snapshot = None
        await self._pages.back(await self._host.page())
        return ToolResult(tool="back", ok=True)

    async def _scroll(self, data: ScrollInput) -> ToolResult:
        if data.ref is not None:
            self._node(data.ref)
        await self._pages.scroll(await self._host.page(), data.ref, down=data.direction == "down")
        return ToolResult(tool="scroll", ok=True)

    async def _wait_for(self, data: WaitForInput) -> ToolResult:
        if data.ms is not None:
            await self._sleep(data.ms / 1000)
            return ToolResult(tool="wait_for", ok=True)
        assert data.text is not None
        page = await self._host.page()
        found = await self._pages.wait_for_text(page, data.text, timeout_ms=WAIT_TEXT_TIMEOUT_MS)
        return ToolResult(tool="wait_for", ok=True, found=found)

    # ------------------------------------------------------------------ 입력 (FillLog)
    async def _fill(self, data: FillInput) -> ToolResult:
        node = self._input_node(data.ref)
        await self._pages.fill(await self._host.page(), data.ref, data.value)
        self._record(FillAction.FILL, node, data.source, value=data.value)
        return ToolResult(tool="fill", ok=True)

    async def _select(self, data: SelectInput) -> ToolResult:
        node = self._input_node(data.ref)
        chosen = await self._pages.select(await self._host.page(), data.ref, data.option)
        self._record(FillAction.SELECT, node, data.source, value=chosen)
        return ToolResult(tool="select", ok=True, message=redact_text(chosen))

    async def _check(self, data: CheckInput) -> ToolResult:
        node = self._input_node(data.ref)
        await self._pages.set_checked(await self._host.page(), data.ref, data.on)
        self._record(FillAction.CHECK, node, data.source, checked=data.on)
        return ToolResult(tool="check", ok=True)

    async def _upload(self, data: UploadInput) -> ToolResult:
        node = self._input_node(data.ref)
        try:
            meta, content = await self._documents.read_document(self._user_id, data.document_id)
        except NotFound as e:
            raise _Refused(ToolError.DOCUMENT_NOT_FOUND, "앱에 등록된 문서가 아니다") from e
        file = UploadFile(name=meta.filename, content_type=meta.content_type, data=content)
        await self._pages.upload(await self._host.page(), data.ref, file)
        self._record(FillAction.UPLOAD, node, None, document_id=meta.id)
        return ToolResult(tool="upload", ok=True)

    async def _report_failure(self, data: ReportFailureInput) -> ToolResult:
        self._failure = redact_text(data.reason)
        return ToolResult(tool="report_failure", ok=True)

    # ------------------------------------------------------------------ 내부
    def _node(self, ref: str) -> SnapshotNode:
        node = None if self._snapshot is None else self._snapshot.node(ref)
        if node is None:
            raise _Refused(ToolError.STALE_REF, f"{ref} 는 최신 snapshot 의 ref 가 아니다")
        return node

    def _input_node(self, ref: str) -> SnapshotNode:
        node = self._node(ref)
        if node.secret:  # 드라이버도 그 순간의 DOM 으로 다시 본다 (절대 규칙 3)
            raise _Refused(ToolError.SECRET_FIELD, "비밀번호·인증 코드 칸은 입력하지 않는다")
        return node

    def _record(
        self, action: FillAction, node: SnapshotNode, source: FillSource | None, **what: Any
    ) -> None:
        assert self._snapshot is not None
        entry = fill_entry(self._fill_log.next_seq, action, self._snapshot, node, source, **what)
        self._fill_log = self._fill_log.append(entry)


def _fail(tool: str, error: ToolError, message: str) -> ToolResult:
    return ToolResult(tool=tool, ok=False, error=error, message=message)
