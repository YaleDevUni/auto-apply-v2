from collections.abc import Callable
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
      - SUPERVISED 에서 체크포인트가 거절/시간초과되면 CheckpointDeclined
        (§ supervised-checkpoint-design)

    `heartbeat` — activity 가 `temporalio.activity.heartbeat`를 plain callable 로 넘긴다.
    체크포인트 대기처럼 오래 걸릴 수 있는 구간에서 워커에 생존신호를 보내는 용도라, 이
    port 는 temporalio 를 몰라도 되게 시그니처만 callable 로 받는다(§11 레이어 규칙 —
    temporalio 는 contracts 에서만 허용). 체크포인트를 안 쓰는 구현(replay/agent_browser)은
    받기만 하고 무시한다.
    """

    async def run(
        self,
        recipe: AutomationRecipe,
        ctx: ExecutionContext,
        mode: ExecutionMode,
        *,
        heartbeat: Callable[[str], None] | None = None,
    ) -> ExecutionResult: ...
