"""fill run 한 번의 도구 통로와 끝난 모양 → 지원 건 상태 (§A6, §A3).

런타임이 부르는 `call_tool` 은 여기서 BrowserToolbox 로 간다. 한도(도구 수·시간)도 여기서 센다 —
런타임이 한도를 지키지 않아도(CLI 프로세스 등) 넘친 호출은 도구에 닿지 않는다. 상태는 에이전트의
말(출력 텍스트)이 아니라 도구 결과로만 정한다.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentOutcome, ToolReply
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.human_gate import HumanTaskKind
from auto_apply.domain.enums import ApplicationState, RunStatus
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.browser_toolbox_specs import TOOLS


class FillSession:
    def __init__(
        self, toolbox: BrowserToolbox, limits: AgentLimits, now: Callable[[], float]
    ) -> None:
        self._toolbox, self._limits, self._now = toolbox, limits, now
        self._deadline = now() + limits.max_seconds
        self.calls = 0
        self.limit: AgentEnd | None = None
        self.guard_unavailable = False

    @property
    def finished(self) -> bool:
        t = self._toolbox
        ended = (t.review, t.failure, t.incident, t.needs_human)
        return any(x is not None for x in ended) or self.guard_unavailable or self.limit is not None

    async def call(self, name: str, args: Mapping[str, object]) -> ToolReply:
        if not self.finished:
            if self.calls >= self._limits.max_tool_calls:
                self.limit = AgentEnd.TOOL_LIMIT
            elif self._now() >= self._deadline:
                self.limit = AgentEnd.TIME_LIMIT
        if self.limit is not None:
            # 이름은 LLM 이 만든 문자열이다 — 모르는 이름은 되돌리지 않는다
            tool = name if name in TOOLS else "unknown"
            result = ToolResult(
                tool=tool, ok=False, error=ToolError.RUN_LIMIT, message="run 한도를 넘어 끝났다"
            )
            return ToolReply(ok=False, content=result.model_dump_json(), done=True)
        self.calls += 1
        result = await self._toolbox.call(name, args)
        if result.error is ToolError.GUARD_UNAVAILABLE:
            # 하네스 없이 페이지를 건드리지 않는다 — 이 run 은 여기서 끝낸다(닫힌 쪽)
            self.guard_unavailable = True
        content = result.model_dump_json(exclude_defaults=True)
        return ToolReply(ok=result.ok, content=content, done=self.finished)


@dataclass(frozen=True, slots=True)
class FillVerdict:
    state: ApplicationState
    run_status: RunStatus
    reason: str


def decide(toolbox: BrowserToolbox, session: FillSession, outcome: AgentOutcome) -> FillVerdict:
    """끝난 run → 지원 건이 갈 곳. 위에서부터 첫 일치 — 제출 흔적이 무엇보다 먼저다."""
    if toolbox.incident is not None:
        return FillVerdict(ApplicationState.INCIDENT, RunStatus.FAILED, "L5: 제출 흔적 감지")
    if toolbox.review is not None:
        return FillVerdict(ApplicationState.AWAITING_APPROVAL, RunStatus.DONE, "ready_for_review")
    if toolbox.needs_human is not None:
        if toolbox.needs_human.kind is HumanTaskKind.LOGIN:
            return FillVerdict(ApplicationState.NEEDS_LOGIN, RunStatus.DONE, "로그인 대기 끝남")
        return FillVerdict(ApplicationState.NEEDS_INPUT, RunStatus.DONE, "사람 확인 대기 끝남")
    if session.guard_unavailable:
        return _failed("하네스를 켜지 못했다 (guard_unavailable)")
    if toolbox.failure is not None:
        return _failed(f"에이전트 보고: {toolbox.failure}")
    limit = session.limit or (None if outcome.ended is AgentEnd.COMPLETED else outcome.ended)
    if limit is AgentEnd.TOOL_LIMIT:
        return _failed("도구 호출 한도를 넘었다")
    if limit is AgentEnd.TIME_LIMIT:
        return _failed("시간 한도를 넘었다")
    return _failed("에이전트가 ready_for_review·report_failure 없이 멈췄다")


def _failed(reason: str) -> FillVerdict:
    return FillVerdict(ApplicationState.FAILED, RunStatus.FAILED, reason)
