from typing import Protocol

from auto_apply.contracts.dto import ExecutionContext, ExecutionResult
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ExecutionMode


class RecipeExecutor(Protocol):
    """브라우저 엔진이 아니라 '실제로 제출하는가' 를 추상화한다 (§11.2).

    계약:
      - Recipe 가 현재 DOM 과 맞지 않으면 RecipeExecutionError(snapshot_key, form_hash)
      - CAPTCHA 를 만나면 CaptchaEncountered (우회 금지)
      - 이미 제출된 상태면 AlreadySubmitted
    """

    async def run(
        self, recipe: AutomationRecipe, ctx: ExecutionContext, mode: ExecutionMode
    ) -> ExecutionResult: ...
