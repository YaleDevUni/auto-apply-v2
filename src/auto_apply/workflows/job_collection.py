"""공고 수집 (ARCHITECTURE.md §2, §11.2b). Temporal Schedule(cron)이 주기적으로 시작한다.

이 파일의 규칙 (§11.3):
  - contracts / domain 만 import
  - 플랫폼 목록은 cmd(입력)로 받는다 — 워크플로우 안에서 config/환경변수를 직접 읽지 않는다
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import collect_platform_jobs
from auto_apply.contracts.job import CollectJobsInput, CollectJobsResult, PlatformCollectionResult

_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["PolicyViolation"])


@workflow.defn
class JobCollectionWorkflow:
    @workflow.run
    async def run(self, cmd: CollectJobsInput) -> CollectJobsResult:
        # 플랫폼마다 독립적인 파이프라인이다. 하나가 느리거나(수백 건 상세 조회)
        # 실패해도 나머지를 막지 않도록 동시에 돌린다.
        results = await asyncio.gather(*(self._collect(p) for p in cmd.platforms))
        return CollectJobsResult(results=list(results))

    async def _collect(self, platform: str) -> PlatformCollectionResult:
        """한 플랫폼의 실패가 다른 플랫폼 결과까지 지우지 않는다 — 받은 데까지 보고한다."""
        try:
            return await workflow.execute_activity(
                collect_platform_jobs,
                platform,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=_RETRY,
            )
        except ActivityError as e:
            # Temporal 은 예외를 ApplicationError 로 감싸며 원래 클래스는 .type 문자열로 남는다
            # (§11.3 "자주 틀리는 곳" — isinstance 가 아니라 type 비교).
            failure_type = e.cause.type if isinstance(e.cause, ApplicationError) else "unknown"
            return PlatformCollectionResult(platform=platform, error=f"{failure_type}: {e.cause}")
