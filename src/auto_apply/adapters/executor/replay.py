"""제출하지 않는 executor.

RecipeExecutor port 의 두 번째 구현이며, Playwright 어댑터(M2)의 테스트 대역이기도 하다.
mode 에 따른 '제출 여부' 결정 로직이 executor 계약의 핵심이므로 여기서도 그대로 지킨다.
"""

from datetime import UTC, datetime

from auto_apply.contracts.dto import ExecutionContext, ExecutionResult
from auto_apply.contracts.recipe import ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import RecipeExecutionError
from auto_apply.ports.clock import Clock


class ReplayExecutor:
    def __init__(
        self,
        clock: Clock,
        *,
        fail_selectors: frozenset[str] = frozenset(),
    ) -> None:
        self._clock = clock
        # 테스트에서 DOM 변경 상황을 재현하기 위한 주입점
        self._fail_selectors = fail_selectors

    async def run(
        self, recipe: AutomationRecipe, ctx: ExecutionContext, mode: ExecutionMode
    ) -> ExecutionResult:
        artifacts: list[str] = []
        for i, action in enumerate(recipe.actions):
            if action.selector and action.selector in self._fail_selectors:
                raise RecipeExecutionError(
                    f"selector 를 찾을 수 없다: {action.selector}",
                    snapshot_key=f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/replay.html",
                    form_hash=recipe.form_hash,
                )
            if action.type is ActionType.FILL and action.value_ref:
                key = action.value_ref.removeprefix("profile.")
                if key not in ctx.profile:
                    raise RecipeExecutionError(
                        f"프로필에 값이 없다: {action.value_ref}",
                        snapshot_key=f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/replay.html",
                        form_hash=recipe.form_hash,
                    )
            if action.type is ActionType.SUBMIT and mode is ExecutionMode.DRY_RUN:
                # dry_run 은 submit 직전까지만 (§2.4)
                return ExecutionResult(
                    outcome=AttemptOutcome.SUCCEEDED,
                    submitted_at=None,
                    artifact_keys=artifacts,
                    detail="dry_run: submit 을 실행하지 않았다",
                )
            artifacts.append(f"application-artifacts/{ctx.application_id}/{ctx.attempt}/{i:02d}")

        submitted: datetime | None = None
        if recipe.has_submit:
            submitted = self._clock.now().astimezone(UTC)
        return ExecutionResult(
            outcome=AttemptOutcome.SUCCEEDED,
            submitted_at=submitted,
            artifact_keys=artifacts,
            detail=f"replay/{mode}",
        )
