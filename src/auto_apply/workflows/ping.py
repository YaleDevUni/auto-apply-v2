"""M0 smoke test 워크플로우.

이 파일이 지켜야 할 규칙 (ARCHITECTURE.md §11.3):
  - contracts / domain 만 import
  - 어댑터·ports·activity 구현을 절대 import 하지 않는다
"""

from datetime import timedelta

from temporalio import workflow

from auto_apply.contracts.activity_defs import ping


@workflow.defn
class PingWorkflow:
    @workflow.run
    async def run(self, message: str) -> str:
        # stub 을 넘기면 Temporal 이 @activity.defn 의 '이름'으로 구현을 찾는다
        return await workflow.execute_activity(
            ping, message, start_to_close_timeout=timedelta(seconds=10)
        )
