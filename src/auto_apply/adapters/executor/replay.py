"""제출하지 않는 executor.

RecipeExecutor port 의 두 번째 구현이며, Playwright 어댑터(M2)의 테스트 대역이기도 하다.
mode 에 따른 '제출 여부' 결정 로직이 executor 계약의 핵심이므로 여기서도 그대로 지킨다.
"""

from datetime import UTC, datetime

from auto_apply.contracts.dto import ExecutionContext, ExecutionResult
from auto_apply.contracts.recipe import ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import AuthRequired, CaptchaEncountered, RecipeExecutionError
from auto_apply.domain.recipe_selector import resolve_selector
from auto_apply.ports.clock import Clock


class ReplayExecutor:
    def __init__(
        self,
        clock: Clock,
        *,
        fail_selectors: frozenset[str] = frozenset(),
        captcha: bool = False,
        authed_platforms: frozenset[str] | None = None,
    ) -> None:
        self._clock = clock
        # 아래 셋 다 테스트에서 실제 브라우저 없이 executor 계약(§11.2)을 재현하는 주입점.
        # PlaywrightExecutor 와 동일한 contract test 를 돌리기 위해 존재한다.
        self._fail_selectors = fail_selectors
        self._captcha = captcha
        # None = 인증 검사 안 함(기존 동작 유지). 값이 있으면 그 목록에만 있는 platform 만 인증됨.
        self._authed_platforms = authed_platforms

    async def run(
        self, recipe: AutomationRecipe, ctx: ExecutionContext, mode: ExecutionMode
    ) -> ExecutionResult:
        if self._authed_platforms is not None and recipe.platform not in self._authed_platforms:
            raise AuthRequired(f"{recipe.platform} 로그인 상태가 없다")
        if self._captcha:
            raise CaptchaEncountered(f"{recipe.platform} 에서 CAPTCHA 감지")

        artifacts: list[str] = []
        for i, action in enumerate(recipe.actions):
            if action.selector:
                try:
                    selector = resolve_selector(action, ctx.profile)
                except ValueError as e:
                    raise RecipeExecutionError(
                        str(e),
                        snapshot_key=(
                            f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/replay.html"
                        ),
                        form_hash=recipe.form_hash,
                    ) from e
                if selector in self._fail_selectors:
                    raise RecipeExecutionError(
                        f"selector 를 찾을 수 없다: {selector}",
                        snapshot_key=(
                            f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/replay.html"
                        ),
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
