"""사람 핸드오프 도구 — request_login·request_human (§A5, 절대 규칙 1·3).

사람은 로그인·SSO·CAPTCHA 폼을 스스로 제출해야 하므로 기다리는 동안 가드를 끈다(§A4 켜고 끄기).
그 틈을 에이전트가 쓰지 못하게: 가드를 끄기 **전에** 대기 표시를 세워 다른 도구 호출을 바로
거부하고(도구 호출은 직렬이라 진행 중인 창도 없다), 사람이 끝냈다고 하면 가드를 다시 켜는 데
성공한 뒤에만 도구를 받는다. 다시 켜지 못하면 GUARD_UNAVAILABLE 이고, 이후 도구도 켜는 것부터라
가드 없이 페이지를 건드리는 동작은 없다. 재개 관찰(L5)은 넘기기 직전 화면과 비교한다 — 그사이
지원이 완료된 흔적이 보이면 INCIDENT 로 멈춘다.
"""

import uuid

from auto_apply.contracts.browser_tools import (
    RequestHumanInput,
    RequestLoginInput,
    ToolError,
    ToolResult,
)
from auto_apply.contracts.human_gate import HumanOutcome, HumanTask, HumanTaskKind
from auto_apply.services.browser_toolbox_base import ToolboxBase
from auto_apply.services.browser_toolbox_record import fail, handoff_signal
from auto_apply.services.browser_toolbox_redact import redact_snapshot, redact_text

_RESUMED = "사람이 끝냈다고 알렸다. 화면이 바뀌었을 수 있다 — snapshot 부터 다시 본다."
_UNRESOLVED = {
    HumanOutcome.TIMED_OUT: "사람이 제한 시간 안에 끝내지 않았다",
    HumanOutcome.DECLINED: "사람이 하지 않겠다고 했다",
}


class HandoffTools(ToolboxBase):
    async def _request_login(self, data: RequestLoginInput) -> ToolResult:
        return await self._hand_over("request_login", HumanTaskKind.LOGIN, site=data.site)

    async def _request_human(self, data: RequestHumanInput) -> ToolResult:
        return await self._hand_over("request_human", HumanTaskKind.INPUT, reason=data.reason)

    async def _hand_over(
        self, tool: str, kind: HumanTaskKind, *, site: str = "", reason: str = ""
    ) -> ToolResult:
        page = await self._page()
        report, evidence = await self._guard.observe(page)
        if evidence is not None:
            return self._stop(tool, evidence, report)
        # 사람에게 보일 근거는 하네스가 직접 본다 — 에이전트의 말(site·reason)과 따로
        seen = redact_snapshot(await self._pages.snapshot(page))
        task = HumanTask(
            id=uuid.uuid4().hex, kind=kind, application_id=self._application_id,
            run_id=self._run_id, site=redact_text(site), reason=redact_text(reason),
            page_url=redact_text(seen.url)[:2048], signal=handoff_signal(seen),
        )  # fmt: skip
        self._snapshot = None
        self._awaiting = task  # 이 순간부터 다른 도구 호출은 바로 거부된다
        try:
            await self._guard.disarm(page)  # strict 꼬리가 남았으면 그동안 기다린 뒤 끈다
            self._armed = None
            self._log.info("toolbox.handoff", kind=kind.value, signal=task.signal)
            reply = await self._gate.wait(task, timeout_s=self._human_wait_s)
        finally:
            self._awaiting = None
        self._log.info("toolbox.handoff_done", kind=kind.value, outcome=reply.outcome.value)
        if reply.outcome is not HumanOutcome.DONE:
            # run 을 끝낸다. 사람이 아직 브라우저를 쓰고 있을 수 있어 가드는 꺼 둔다(close 와 같다)
            self._needs_human = task
            error = ToolError.NEEDS_LOGIN if kind is HumanTaskKind.LOGIN else ToolError.NEEDS_INPUT
            return fail(tool, error, f"{_UNRESOLVED[reply.outcome]} — run 을 끝낸다", report)
        page = await self._page()  # 다시 켠다 — 못 켜면 SubmitGuardUnavailable (도구 거부)
        after, evidence = await self._guard.observe(page)
        if evidence is not None:
            return self._stop(tool, evidence, report + after)
        note = redact_text(reply.note)
        message = f"{_RESUMED} 사람의 말: {note}" if note else _RESUMED
        return ToolResult(tool=tool, ok=True, message=message, guard=report + after)
