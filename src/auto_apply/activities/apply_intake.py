"""자동 지원 시작 activity — `ApplyIntakeWorkflow`가 Schedule(cron)로 주기 실행할 때 부른다.

실제 로직(TTL 캐시 조회 → dedupe → `ApplicationWorkflow` 시작)은 새로 만들지 않는다 —
`auto_apply.apply_intake.start_actionable_applications`가 이미 CLI/텔레그램 채팅 도구에서
쓰는 운영 진입점이라 그대로 재사용한다. 다른 activity 들과 달리 개별 port 대신 `Container`를
그대로 받는다 — 그 함수가 세 호출부(CLI·채팅 도구·이 activity)에서 정확히 같은 시그니처를
유지하길 원해서다(포트별로 쪼개면 호출부마다 다시 조립해야 한다).
"""

from collections.abc import Callable
from typing import Any

from temporalio import activity
from temporalio.client import Client

from auto_apply.apply_intake import start_actionable_applications as _start_actionable_applications
from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult


class ApplyIntakeActivities:
    def __init__(self, container: Container, client: Client) -> None:
        self._c = container
        self._client = client

    @activity.defn(name="start_actionable_applications")
    async def start_actionable_applications(self, cmd: ApplyIntakeInput) -> ApplyIntakeResult:
        result = await _start_actionable_applications(cmd.count, self._c, self._client)
        return ApplyIntakeResult(
            started=result.started, skipped=result.skipped, candidates=result.candidates
        )

    def all(self) -> list[Callable[..., Any]]:
        return [self.start_actionable_applications]
