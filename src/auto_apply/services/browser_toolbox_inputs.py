"""입력 도구 — fill·select·check·upload (§A5). 성공하면 FillLog 에 근거와 함께 남는다.

넷 다 페이지의 input·change 핸들러를 부르므로 하네스 창 안에서 돈다(§A4). check 는 요소를 클릭하므로
click 과 같은 L2 분류로 창 모드를 고른다(T2.4 이관 — onchange 발신).
"""

from functools import partial

from auto_apply.contracts.browser_tools import (
    CheckInput,
    FillInput,
    SelectInput,
    ToolError,
    ToolResult,
    UploadInput,
)
from auto_apply.contracts.fill_log import FillAction
from auto_apply.contracts.page import UploadFile
from auto_apply.domain.errors import NotFound
from auto_apply.domain.page_elements import normalize_fill_value
from auto_apply.domain.submit_guard_policy import GuardMode
from auto_apply.services.browser_toolbox_base import Refused, ToolboxBase
from auto_apply.services.browser_toolbox_redact import redact_text


class InputTools(ToolboxBase):
    async def _fill(self, data: FillInput) -> ToolResult:
        node = self._input_node(data.ref)
        # 브라우저가 한 줄 칸의 줄바꿈을 지운다 — 정리한 값을 넣고 그 값을 기록해
        # DOM 과 기록이 같게(§A4 L6).
        editable = node.tag not in ("input", "textarea")
        value = normalize_fill_value(
            node.tag, node.input_type, data.value, contenteditable=editable
        )
        page = await self._page()

        def done(_: object) -> ToolResult:
            self._record(FillAction.FILL, node, data.source, value=value)
            return ToolResult(tool="fill", ok=True)

        action = partial(self._pages.fill, page, data.ref, value)
        return await self._guarded("fill", page, GuardMode.RELAXED, action, done)

    async def _select(self, data: SelectInput) -> ToolResult:
        node = self._input_node(data.ref)
        page = await self._page()

        def done(chosen: str) -> ToolResult:
            self._record(FillAction.SELECT, node, data.source, value=chosen)
            return ToolResult(tool="select", ok=True, message=redact_text(chosen))

        action = partial(self._pages.select, page, data.ref, data.option)
        return await self._guarded("select", page, GuardMode.RELAXED, action, done)

    async def _check(self, data: CheckInput) -> ToolResult:
        node = self._input_node(data.ref)
        page = await self._page()
        mode, _ = await self._guard.click_mode(page, data.ref)
        if mode is GuardMode.STEP:  # 단계 이동 창은 click 만 연다(사후 확인이 거기 있다) — D17
            mode = GuardMode.STRICT

        def done(_: object) -> ToolResult:
            self._record(FillAction.CHECK, node, data.source, checked=data.on)
            return ToolResult(tool="check", ok=True)

        action = partial(self._pages.set_checked, page, data.ref, data.on)
        return await self._guarded("check", page, mode, action, done)

    async def _upload(self, data: UploadInput) -> ToolResult:
        node = self._input_node(data.ref)
        try:
            meta, content = await self._documents.read_document(self._user_id, data.document_id)
        except NotFound as e:
            raise Refused(ToolError.DOCUMENT_NOT_FOUND, "앱에 등록된 문서가 아니다") from e
        file = UploadFile(name=meta.filename, content_type=meta.content_type, data=content)
        page = await self._page()

        def done(_: object) -> ToolResult:
            self._record(FillAction.UPLOAD, node, None, document_id=meta.id)
            return ToolResult(tool="upload", ok=True)

        action = partial(self._pages.upload, page, data.ref, file)
        return await self._guarded("upload", page, GuardMode.RELAXED, action, done)
