"""자동 지원 시작 루틴 (§ apply-schedule). Temporal Schedule(cron)이 주기적으로 시작한다.

`JobCollectionWorkflow`가 채워둔 actionable 공고 캐시에서 적합도 상위 N건에 대해
`ApplicationWorkflow`를 새로 시작한다(실제 dedupe/TTL 로직은 `apply_intake.py`, activity가
그대로 재사용한다). 실제 최종 제출은 여전히 그렇게 시작된 `ApplicationWorkflow` 안의 텔레그램
승인 뒤에서만 일어난다 — 이 워크플로우는 "지원 프로세스 시작"(이력서 생성·승인 대기 진입)까지만
자동화한다(CLAUDE.md 절대규칙 4 — "워크플로우를 안 건드린다"가 아니라 "제출은 못 건드린다").

이 파일의 규칙 (§11.3): contracts/domain 만 import.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from auto_apply.contracts.activity_defs import notify, start_actionable_applications
from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult, NotifyEvent
from auto_apply.domain.alerting import intake_alert
from auto_apply.workflows._errors import activity_failure

# 활동 자체가 `ApplicationWorkflow` 시작 여러 건을 REJECT_DUPLICATE dedupe 로 감싸므로
# (apply_intake.py), 전체를 재시도해도 이미 시작된 건 WorkflowAlreadyStartedError 로 안전하게
# 건너뛴다 — JobCollectionWorkflow 와 같은 상한(3회)을 쓴다.
_RETRY = RetryPolicy(maximum_attempts=3)
_NOTIFY_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))


@workflow.defn
class ApplyIntakeWorkflow:
    @workflow.run
    async def run(self, cmd: ApplyIntakeInput) -> ApplyIntakeResult:
        try:
            result = await workflow.execute_activity(
                start_actionable_applications,
                cmd,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=_RETRY,
            )
        except ActivityError as e:
            # 재시도까지 다 쓰고 실패하면 워크플로우가 FAILED 로 죽는다 — watchdog 이 돌고
            # 있어야만 사람이 안다. 이건 사람이 안 시킨 시각(cron)에 도는 루틴이라 감시
            # 프로세스 하나에 알림을 전부 걸지 않고 여기서도 직접 알린다(중복 알림 감수 —
            # watchdog.py 모듈 docstring 과 같은 판단).
            _, reason = activity_failure(e)
            await self._alert(f"자동 지원 시작이 실패했다: {reason}")
            raise
        await self._alert(
            intake_alert(
                started=len(result.started),
                skipped=len(result.skipped),
                candidates=result.candidates,
            )
        )
        return result

    async def _alert(self, message: str | None) -> None:
        """알림 전송 실패가 이 워크플로우의 결론을 뒤집지 않게 한다 — 실패 경로에서는 원래
        예외를, 성공 경로에서는 시작된 지원 건 목록을 그대로 살려야 한다.
        """
        if message is None:
            return
        try:
            await workflow.execute_activity(
                notify,
                NotifyEvent(kind="APPLY_INTAKE_UNHEALTHY", message=message),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_NOTIFY_RETRY,
            )
        except ActivityError:
            workflow.logger.warning("apply_intake.alert_failed")
