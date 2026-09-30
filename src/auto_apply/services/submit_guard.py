"""SubmitGuard — 도구 동작 하나를 하네스 창 안에서 실행한다 (§A4 L2·L3·L5, D17).

순서: 창 사이 보고 수거 → 모드 결정(클릭은 L2 분류) → 창 열기 → 동작 → 후속이 잦아들 때까지
→ 창 보고 수거 → 사후 감지(L5). 사후 감지는 매 관찰마다 **직전 관찰과** 비교한다 — 창 사이에
뜬 완료 문구가 다음 동작의 "원래 있던 문구"로 묻히지 않게. strict 창 뒤에는 꼬리(STRICT_TAIL_S)
동안 strict 가 아닌 창을 열지 않고, 가드를 끄는 것도 꼬리가 지난 뒤다. step 창(단계 이동)은 끝나면
relaxed 로 되돌리고, 결과 화면을 `after_step` 이 확인한다.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from auto_apply.contracts.click import (
    ClickRisk,
    ClickVerdict,
    ElementDescriptor,
    RiskyClick,
    StepClick,
)
from auto_apply.contracts.submit_guard import GuardReport, PageText
from auto_apply.domain.errors import PageActionFailed
from auto_apply.domain.submit_classifier import classify_click
from auto_apply.domain.submit_guard_policy import (
    GuardMode,
    StepLanding,
    carried_values,
    completion_evidence,
    step_landing,
)
from auto_apply.ports.browser import GuardedPageDriver, PageHandle


def classify_target(
    descriptors: Sequence[ElementDescriptor], page: PageText | None = None
) -> ClickVerdict:
    """요소와 클릭이 닿는 조상(가장 안쪽부터)을 모두 분류해 하나라도 Risky 면 Risky, 아니면
    하나라도 Step 이면 Step (§A4 L2, D17).

    `role=checkbox` span 을 품은 submit 버튼처럼, 안쪽이 안전해 보여도 클릭은 조상에 닿는다.
    """
    verdicts = [classify_click(d, page) for d in descriptors]
    risky = next((v for v in verdicts if isinstance(v, RiskyClick)), None)
    if risky is not None or not verdicts:
        return risky or RiskyClick(reason=ClickRisk.UNRECOGNIZED)
    return next((v for v in verdicts if isinstance(v, StepClick)), verdicts[0])


_MODES = {"risky": GuardMode.STRICT, "step": GuardMode.STEP, "safe": GuardMode.RELAXED}


@dataclass(frozen=True, slots=True)
class GuardedOutcome[T]:
    value: T | None
    error: PageActionFailed | None  # 동작 자체의 실패 — 막힘·사고가 있으면 그쪽이 먼저다
    report: GuardReport  # 창 사이 + 창 안
    blocked_in_window: bool
    evidence: str | None  # L5 완료 근거
    before: PageText | None = None  # 창 직전 관찰 — 단계 이동 뒤 화면이 바뀌었는지 (D17)


# strict 창 뒤 이 시간 안에는 relaxed 창을 열지 않고 기다린다 — risky 클릭 핸들러가 setTimeout
# 으로 미룬 제출이 곧바로 이어진 안전한 동작의 relaxed 창에 섞여 새지 않게.
# 더 늦는 제출은 L5 가 본다.
STRICT_TAIL_S = 3.0
# 단계 이동 뒤 입력칸이 안 보이면 이만큼 더 지켜본다 — 다음 단계를 늦게 그리는 SPA 를 사람에게
# 넘기지 않게.
STEP_LANDING_S = 2.0
_STEP_LOOKS = 4


class SubmitGuard:
    def __init__(
        self,
        driver: GuardedPageDriver,
        *,
        forbidden_origins: Iterable[str],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._driver = driver
        self._forbidden = tuple(forbidden_origins)
        self._sleep, self._clock = sleep, clock
        self._baseline: PageText | None = None
        self._strict_until = 0.0

    async def arm(self, page: PageHandle) -> None:
        await self._driver.arm(page, forbidden_origins=self._forbidden)

    async def disarm(self, page: PageHandle) -> None:
        """run 이 끝났다. strict 꼬리가 남았으면 그동안 strict 로 기다린 뒤 끈다 — risky 클릭 직후
        ready_for_review 로 run 을 끝내도 미뤄 둔 제출이 꺼진 가드로 새지 않게."""
        if (wait := self._strict_until - self._clock()) > 0:
            await self._sleep(wait)
        await self._driver.disarm(page)

    @property
    def last_seen(self) -> PageText | None:
        """가장 최근 관찰 (L5 기준선)."""
        return self._baseline

    async def click_mode(self, page: PageHandle, ref: str) -> tuple[GuardMode, ClickVerdict]:
        """L2 → L3 — 클릭 대상 판정과 그 창의 모드.

        단계 어휘 버튼은 지금 화면의 마지막 단계 신호를 본다(D17). step 창은 strict 꼬리 뒤에
        열리므로 꼬리가 남았으면 기다린 뒤의 화면으로 다시 판정한다 — 그사이 화면이 마지막 단계로
        바뀌었을 수 있다.
        """
        verdict = await self._classify(page, ref)
        if verdict.kind == "step" and (wait := self._strict_until - self._clock()) > 0:
            await self._sleep(wait)
            verdict = await self._classify(page, ref)
        return _MODES[verdict.kind], verdict

    async def _classify(self, page: PageHandle, ref: str) -> ClickVerdict:
        page_now = await self._driver.read_text(page)  # 기준선은 건드리지 않는다 (L5 는 run 이)
        return classify_target(await self._driver.describe(page, ref), page_now)

    async def observe(self, page: PageHandle) -> tuple[GuardReport, str | None]:
        """창 밖 관찰 — 직전 뒤로 막은 것과 새로 나타난 완료 근거."""
        return await self._driver.drain(page), await self._look(page)

    async def run[T](
        self,
        page: PageHandle,
        mode: GuardMode,
        action: Callable[[], Awaitable[T]],
        *,
        carried: Iterable[str] = (),
    ) -> GuardedOutcome[T]:
        if mode is not GuardMode.STRICT and (wait := self._strict_until - self._clock()) > 0:
            await self._sleep(wait)  # 그동안 모드는 strict 로 남아 있다
        idle, idle_evidence = await self.observe(page)
        if idle_evidence is not None:  # 창 사이에 이미 뚫렸다 — 더 건드리지 않는다
            return GuardedOutcome(None, None, idle, False, idle_evidence)
        before = self._baseline
        typed = carried_values(carried)
        await self._driver.set_window(page, mode, carried=typed)
        value: T | None = None
        error: PageActionFailed | None = None
        try:
            try:
                value = await action()
            except PageActionFailed as e:
                error = e
            await self._driver.settle(page)
        finally:
            # 동작이 뜻밖의 예외로 끝나도 핸들러는 이미 돌았을 수 있다 — 꼬리는 반드시 건다.
            if mode is GuardMode.STRICT:
                self._strict_until = self._clock() + STRICT_TAIL_S
            elif mode is GuardMode.STEP:  # 단계 이동 허용은 이 창뿐이다 — 창 사이는 relaxed
                await self._driver.set_window(page, GuardMode.RELAXED, carried=typed)
        during = await self._driver.drain(page)
        evidence = await self._look(page)
        return GuardedOutcome(value, error, idle + during, bool(during.blocked), evidence, before)

    async def after_step[T](
        self, page: PageHandle, out: GuardedOutcome[T]
    ) -> tuple[StepLanding, GuardReport, str | None]:
        """step 창 뒤 화면 (D17) — (도착한 곳, 그동안 막은 것, L5 완료 근거).

        할 일이 안 보이면 `STEP_LANDING_S` 동안 다시 본다. 그래도 없으면 UNCLEAR — 애매하면 닫힌 쪽.
        """
        report = GuardReport()
        landing = StepLanding.UNCLEAR
        for look in range(_STEP_LOOKS + 1):
            if look:
                await self._sleep(STEP_LANDING_S / _STEP_LOOKS)
                more, evidence = await self.observe(page)
                report += more
                if evidence is not None:
                    return StepLanding.UNCLEAR, report, evidence
            if self._baseline is not None:
                landing = step_landing(out.before, self._baseline)
            if landing is not StepLanding.UNCLEAR:
                break
        return landing, report, None

    async def _look(self, page: PageHandle) -> str | None:
        now = await self._driver.read_text(page)
        before, self._baseline = self._baseline, now
        if before is None:
            return None
        return completion_evidence(before.urls, before.lines, now.urls, now.lines)
