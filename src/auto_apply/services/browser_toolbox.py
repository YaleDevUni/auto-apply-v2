"""BrowserToolbox — 에이전트 도구의 실행 (§A5). 도구 정의는 browser_toolbox_specs.TOOLS 하나다.

LLM 이 만든 인자는 `call()` 에서 Pydantic 검증을 통과해야만 브라우저에 닿는다(절대 규칙 4). 페이지를
건드리는 도구는 전부 SubmitGuard 창 안에서 돈다(§A4) — 하네스를 못 켜면 동작하지 않는다.
fill·select·check·upload 가 성공하면 FillLog 에 근거와 함께 남긴다 — 에이전트는 기록을 직접 쓰지
못한다. 도구 호출은 한 번에 하나씩 돌고, 사람을 기다리는 동안(request_login·request_human)은
받지 않는다.
"""

from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Any

from pydantic import BaseModel, ValidationError

from auto_apply.contracts.browser_tools import (
    ClickInput,
    NavigateInput,
    ReadyForReviewInput,
    ReportFailureInput,
    ScrollInput,
    ToolError,
    ToolResult,
    WaitForInput,
)
from auto_apply.contracts.submit_guard import ReviewRecord
from auto_apply.domain.errors import PageActionFailed, SubmitGuardUnavailable
from auto_apply.domain.submit_guard_policy import GuardMode, carries_input
from auto_apply.domain.url_policy import is_forbidden_url
from auto_apply.services.browser_toolbox_base import DocumentReader, Refused
from auto_apply.services.browser_toolbox_handoff import HandoffTools
from auto_apply.services.browser_toolbox_inputs import InputTools
from auto_apply.services.browser_toolbox_record import (
    HANDOFF_HINTS,
    describe_validation_error,
    fail,
    handoff_signal,
)
from auto_apply.services.browser_toolbox_redact import redact_snapshot, redact_target, redact_text
from auto_apply.services.browser_toolbox_specs import TOOLS
from auto_apply.services.submit_guard import classify_target

__all__ = ["BrowserToolbox", "DocumentReader"]
WAIT_TEXT_TIMEOUT_MS = 10_000
_HANDLERS = {
    "snapshot": "_do_snapshot", "navigate": "_navigate", "back": "_back", "scroll": "_scroll",
    "wait_for": "_wait_for", "click": "_click", "fill": "_fill", "select": "_select",
    "check": "_check", "upload": "_upload", "ready_for_review": "_ready_for_review",
    "report_failure": "_report_failure", "request_login": "_request_login",
    "request_human": "_request_human",
}  # fmt: skip
assert _HANDLERS.keys() == TOOLS.keys()  # 정의와 실행이 어긋나면 import 부터 실패


class BrowserToolbox(InputTools, HandoffTools):
    """run 하나의 도구 상자. 탭은 BrowserHost 의 작업 탭 하나다(새 탭·팝업 조작은 없다).

    `step` 은 다단계 사이트에서 몇 번째 승인 단계의 FILL 인지 — FillLog 항목과
    ready_for_review 에 붙는다. run 이 끝나면 `close()` 로 가드를 내린다.
    """

    async def call(self, tool: str, args: Mapping[str, object] | None = None) -> ToolResult:
        """에이전트 도구 호출의 유일한 입구."""
        if self._awaiting is not None:
            # 가드가 꺼진 동안이다 — 잠금을 기다리지 않고 바로 거부한다 (§A5 핸드오프)
            name = tool if tool in TOOLS else "unknown"
            result = fail(name, ToolError.AWAITING_HUMAN, "사람이 끝낼 때까지 도구를 받지 않는다")
        else:
            async with self._serial:
                result = await self._call(tool, args or {})
        self._log.info("toolbox.call", tool=result.tool, ok=result.ok, error=result.error)
        return result

    async def _call(self, tool: str, args: Mapping[str, object]) -> ToolResult:
        spec = TOOLS.get(tool)
        if spec is None:
            # 이름도 LLM 이 만든 문자열이다 — 결과·로그에 그대로 되돌리지 않는다.
            return fail("unknown", ToolError.UNKNOWN_TOOL, "없는 도구")
        if self._incident is not None:
            return fail(tool, ToolError.INCIDENT, "제출 흔적이 감지돼 멈춘 run 이다")
        if self._failure is not None or self._review is not None or self._needs_human is not None:
            return fail(tool, ToolError.RUN_FINISHED, "끝난 run 이다")
        try:
            data = spec.input_model.model_validate(dict(args))
        except ValidationError as e:
            return fail(tool, ToolError.INVALID_INPUT, describe_validation_error(e))
        handler: Callable[[Any], Awaitable[ToolResult]] = getattr(self, _HANDLERS[tool])
        try:
            return await handler(data)
        except Refused as r:
            return fail(tool, r.error, str(r))
        except PageActionFailed as e:
            return fail(tool, ToolError(e.reason.value), str(e))
        except SubmitGuardUnavailable:
            self._log.warning("submit_guard.unavailable", tool=tool)
            return fail(tool, ToolError.GUARD_UNAVAILABLE, "하네스를 켜지 못해 동작하지 않았다")

    # ------------------------------------------------------------------ 관찰·이동
    async def _do_snapshot(self, _: BaseModel) -> ToolResult:
        page = await self._page()
        report, evidence = await self._guard.observe(page)
        if evidence is not None:
            return self._stop("snapshot", evidence, report)
        self._snapshot = redact_snapshot(await self._pages.snapshot(page))
        signal = handoff_signal(self._snapshot)
        return ToolResult(
            tool="snapshot", ok=True, snapshot=self._snapshot, guard=report, handoff=signal,
            message=HANDOFF_HINTS[signal] if signal is not None else "",
        )  # fmt: skip

    async def _navigate(self, data: NavigateInput) -> ToolResult:
        if is_forbidden_url(data.url, self._forbidden):
            raise Refused(ToolError.FORBIDDEN_URL, "열 수 없는 주소다")
        if carries_input(data.url, self._typed_values()):
            # 입력한 값을 URL 에 실어 여는 것은 GET 제출 통로다 (§A4 L3, T2.3 이관)
            raise Refused(ToolError.FORBIDDEN_URL, "입력한 값이 실린 주소는 열지 않는다")
        page = await self._page()
        self._snapshot = None
        action = partial(self._pages.navigate, page, data.url)
        return await self._guarded("navigate", page, GuardMode.RELAXED, action)

    async def _back(self, _: BaseModel) -> ToolResult:
        page = await self._page()
        self._snapshot = None
        return await self._guarded("back", page, GuardMode.RELAXED, partial(self._pages.back, page))

    async def _scroll(self, data: ScrollInput) -> ToolResult:
        if data.ref is not None:
            self._node(data.ref)
        page = await self._page()
        action = partial(self._pages.scroll, page, data.ref, down=data.direction == "down")
        return await self._guarded("scroll", page, GuardMode.RELAXED, action)

    async def _wait_for(self, data: WaitForInput) -> ToolResult:
        page = await self._page()
        found: bool | None = None
        if data.ms is not None:
            await self._sleep(data.ms / 1000)
        else:
            assert data.text is not None
            found = await self._pages.wait_for_text(
                page, data.text, timeout_ms=WAIT_TEXT_TIMEOUT_MS
            )
        report, evidence = await self._guard.observe(page)  # 기다리는 사이 사이트가 한 일
        if evidence is not None:
            return self._stop("wait_for", evidence, report)
        return ToolResult(tool="wait_for", ok=True, found=found, guard=report)

    async def _click(self, data: ClickInput) -> ToolResult:
        self._touchable(data.ref)
        page = await self._page()
        mode, _ = await self._guard.click_mode(page, data.ref)  # L2 → 창 모드 (L3)
        return await self._guarded("click", page, mode, partial(self._pages.click, page, data.ref))

    # ------------------------------------------------------------------ 끝내기
    async def _ready_for_review(self, data: ReadyForReviewInput) -> ToolResult:
        self._node(data.submit_ref)
        page = await self._page()
        report, evidence = await self._guard.observe(page)
        if evidence is not None:
            return self._stop("ready_for_review", evidence, report)
        target = redact_target(await self._pages.submit_target(page, data.submit_ref))
        self._review = ReviewRecord(
            target=target,
            verdict=classify_target((target.element, *target.ancestors)),
            notes=redact_text(data.notes),
            step=self._step,
            fill_log=self._fill_log,
        )
        self._log.info("toolbox.ready_for_review", step=self._step)
        message = "승인 대기로 넘겼다 — 사람이 승인하면 하네스가 누른다. run 은 끝났다"
        return ToolResult(tool="ready_for_review", ok=True, message=message, guard=report)

    async def _report_failure(self, data: ReportFailureInput) -> ToolResult:
        self._failure = redact_text(data.reason)
        return ToolResult(tool="report_failure", ok=True)
