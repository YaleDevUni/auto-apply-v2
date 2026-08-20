"""워크플로우 sandbox 검증용 fixture — 최소 의존성 모듈에 둬야 한다.

Temporal 워커는 워크플로우를 등록할 때 그 워크플로우가 정의된 *모듈 전체*를 sandbox 안에서
다시 import 해 결정성을 검증한다. `test_watchdog.py`처럼 pytest/dataclasses/여러 테스트
헬퍼를 잔뜩 import 하는 모듈에 워크플로우를 같이 정의하면 그 부수 import 체인이 sandbox의
`random.getrandbits` 등 제한된 호출을 건드려 검증이 실패한다(실측) — `workflows/ping.py`가
왜 그렇게 얇은 파일인지와 같은 이유다.
"""

from temporalio import workflow
from temporalio.exceptions import ApplicationError


@workflow.defn
class AlwaysFailWorkflow:
    @workflow.run
    async def run(self) -> None:
        raise ApplicationError("boom", type="TestFailure", non_retryable=True)
