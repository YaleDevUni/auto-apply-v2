"""click 도구 — L2 분류로 창 모드를 정하고, 단계 이동(D17)은 결과 화면을 사후 확인한다 (§A4).

단계 이동 버튼(Step)은 그 버튼의 폼 제출만 통과시킨다. 결과가 완료 근거면 INCIDENT(L5), 새 입력
화면(입력칸이나 폼 제출 버튼이 있다)이면 계속(페이지 단계 +1), 둘 다 없으면 제출됐는지 모르는
것이라 run 을 멈추고 사람에게 넘긴다(request_human 과 같은 핸드오프) — 애매하면 닫힌 쪽.
"""

from functools import partial

from auto_apply.contracts.browser_tools import ClickInput, ToolResult
from auto_apply.contracts.human_gate import HumanTaskKind
from auto_apply.domain.submit_guard_policy import GuardMode, StepLanding
from auto_apply.services.browser_toolbox_handoff import HandoffTools

STEP_UNCLEAR_REASON = (
    "단계 이동 버튼을 눌렀더니 입력칸·제출 버튼도 완료 문구도 없는 화면이 나왔다"
    " — 제출됐는지 하네스가 가릴 수 없다. 화면을 확인하고,"
    " 지원이 끝나지 않았으면 이어서 진행해도 된다고 알려 달라."
)
_LANDED = {
    StepLanding.NEXT_STEP: "단계 이동으로 통과했다(D17) — {step}단계 화면이다."
    " snapshot 으로 새 칸을 본다.",
    StepLanding.SAME_PAGE: "단계 이동 버튼을 눌렀지만 화면이 그대로다"
    " — 필수 칸·오류 문구를 snapshot 으로 확인한다.",
}


class ClickTools(HandoffTools):
    async def _click(self, data: ClickInput) -> ToolResult:
        self._touchable(data.ref)
        page = await self._page()
        mode, _ = await self._guard.click_mode(page, data.ref)  # L2 → 창 모드 (L3, D17)
        action = partial(self._pages.click, page, data.ref)
        if mode is not GuardMode.STEP:
            return await self._guarded("click", page, mode, action)
        out = await self._guard.run(page, mode, action, carried=self._typed_values())
        result = self._outcome("click", mode, out)
        if not result.ok:
            return result
        landing, report, evidence = await self._guard.after_step(page, out)
        if evidence is not None:
            return self._stop("click", evidence, result.guard + report)
        self._snapshot = None  # 화면이 바뀌었을 수 있다 — 옛 ref 로 누르지 않게
        self._log.info("submit_guard.step", landing=landing.value, step=self._step)
        if landing is StepLanding.UNCLEAR:
            handed = await self._hand_over("click", HumanTaskKind.INPUT, reason=STEP_UNCLEAR_REASON)
            return handed.model_copy(update={"guard": result.guard + report + handed.guard})
        if landing is StepLanding.NEXT_STEP:
            self._step += 1
        message = _LANDED[landing].format(step=self._step)
        return result.model_copy(update={"message": message, "guard": result.guard + report})
