"""작업 큐·run 기록 port (§A9, §A3). DTO 는 `contracts/jobs.py`.

JobRunner 만 쓴다. 지원 건 상태는 여기서 바꾸지 않는다 — 그건 `ApplicationService.transition()` 몫.
"""

from datetime import datetime
from typing import Protocol

from auto_apply.contracts.jobs import JobKind, JobRecord, JobStatus, RunRecord
from auto_apply.domain.enums import RunStatus


class JobRepository(Protocol):
    async def enqueue(self, job: JobRecord) -> None:
        """QUEUED 행 1개. 같은 id 가 있으면 `InvalidInput`."""
        ...

    async def claim_next(self, kinds: frozenset[JobKind], *, now: datetime) -> JobRecord | None:
        """`kinds` 중 `run_after <= now` 인 가장 오래된 QUEUED 를 RUNNING 으로 바꿔 돌려준다.

        비교와 쓰기가 한 번이라 둘이 같은 행을 꺼내지 않는다.
        """
        ...

    async def finish(
        self, job_id: str, status: JobStatus, *, at: datetime, error: str | None = None
    ) -> None:
        """RUNNING → DONE·FAILED. 없는 job 이면 `NotFound`."""
        ...

    async def requeue(self, job_id: str, *, attempt: int, run_after: datetime, error: str) -> None:
        """RUNNING → QUEUED (재시도). 없는 job 이면 `NotFound`."""
        ...

    async def interrupt_running(self, *, at: datetime) -> list[JobRecord]:
        """RUNNING 전부 → INTERRUPTED. 바꾸기 전 행을 돌려준다."""
        ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def list_by_status(self, status: JobStatus) -> list[JobRecord]:
        """오래된 순."""
        ...


class RunRepository(Protocol):
    async def start(self, run: RunRecord) -> None:
        """RUNNING 행 1개. 같은 id 가 있으면 `InvalidInput`."""
        ...

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        at: datetime,
        result: str | None = None,
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        transcript_path: str | None = None,
    ) -> None:
        """RUNNING 인 run 만 닫는다 — 이미 닫혔으면(복구가 먼저 INTERRUPTED) `InvalidInput`,
        없으면 `NotFound`."""
        ...

    async def interrupt_running(self, *, at: datetime) -> list[str]:
        """RUNNING 전부 → INTERRUPTED. 바꾼 run id 들."""
        ...

    async def get(self, run_id: str) -> RunRecord | None: ...
