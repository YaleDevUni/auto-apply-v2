"""브라우저 실행 activity.

여기가 도메인 에러 → Temporal 실패 로 번역되는 경계다. Temporal 은 예외 '이름' 으로
재시도를 판단하므로, 도메인 에러를 ApplicationError 로 감싸 type 과 details 를 실어 보낸다.
"""

from collections.abc import Callable
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from auto_apply.contracts.dto import ExecuteInput, ExecutionResult
from auto_apply.domain.errors import (
    AuthRequired,
    CaptchaEncountered,
    CheckpointDeclined,
    RecipeExecutionError,
)
from auto_apply.ports.executor import RecipeExecutor


class BrowserActivities:
    def __init__(self, executor: RecipeExecutor) -> None:
        self._executor = executor

    @activity.defn(name="execute_application")
    async def execute_application(self, inp: ExecuteInput) -> ExecutionResult:
        try:
            return await self._executor.run(
                inp.recipe, inp.ctx, inp.mode, heartbeat=activity.heartbeat
            )
        except RecipeExecutionError as e:
            # details 로 snapshot_key / form_hash / failed_action_index 를 넘겨
            # RepairWorkflow(M4) 가 쓸 수 있게 한다.
            raise ApplicationError(
                str(e),
                e.snapshot_key,
                e.form_hash,
                e.failed_action_index,
                type=type(e).__name__,
                non_retryable=True,
            ) from e
        except (CaptchaEncountered, AuthRequired, CheckpointDeclined) as e:
            raise ApplicationError(str(e), type=type(e).__name__, non_retryable=True) from e

    def all(self) -> list[Callable[..., Any]]:
        return [self.execute_application]
