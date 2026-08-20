"""BrowserActivities — 도메인 에러 → ApplicationError 번역만 오프라인으로 확인한다.

execute_application 자체가 RecipeExecutor.run 을 그대로 위임하므로, 실제 실행 계약은
tests/ports/test_executor_contract.py 가 커버한다. 여기서는 activities/browser.py 가
CheckpointDeclined(§ supervised-checkpoint-design) 을 다른 non-retryable 에러들과 같은
방식으로 감싸는지만 본다.
"""

import pytest
from temporalio.exceptions import ApplicationError

from auto_apply.activities.browser import BrowserActivities
from auto_apply.contracts.dto import ExecuteInput, ExecutionContext
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import ExecutionMode
from auto_apply.domain.errors import CheckpointDeclined


class _DecliningExecutor:
    async def run(self, recipe, ctx, mode, *, heartbeat=None):
        raise CheckpointDeclined("00:submit: 체크포인트가 거절됐다")


def _input() -> ExecuteInput:
    recipe = AutomationRecipe(
        platform="fixture",
        version=1,
        status="active",
        form_hash="h-1",
        actions=[Action(type=ActionType.SUBMIT, selector="#submit")],
        success_signals=["완료"],
    )
    return ExecuteInput(
        recipe=recipe,
        ctx=ExecutionContext(application_id="app_1", attempt=1),
        mode=ExecutionMode.SUPERVISED,
    )


async def test_checkpoint_declined_becomes_non_retryable_application_error():
    activities = BrowserActivities(_DecliningExecutor())

    with pytest.raises(ApplicationError) as exc_info:
        await activities.execute_application(_input())

    assert exc_info.value.type == "CheckpointDeclined"
    assert exc_info.value.non_retryable
