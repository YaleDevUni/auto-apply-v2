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

from auto_apply.contracts.activity_defs import start_actionable_applications
from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult

# 활동 자체가 `ApplicationWorkflow` 시작 여러 건을 REJECT_DUPLICATE dedupe 로 감싸므로
# (apply_intake.py), 전체를 재시도해도 이미 시작된 건 WorkflowAlreadyStartedError 로 안전하게
# 건너뛴다 — JobCollectionWorkflow 와 같은 상한(3회)을 쓴다.
_RETRY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class ApplyIntakeWorkflow:
    @workflow.run
    async def run(self, cmd: ApplyIntakeInput) -> ApplyIntakeResult:
        return await workflow.execute_activity(
            start_actionable_applications,
            cmd,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=_RETRY,
        )
