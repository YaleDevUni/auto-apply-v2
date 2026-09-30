"""작업 큐·run 기록 port (§A9, §A3).

JobRunner 만 쓴다. 지원 건 상태는 여기서 바꾸지 않는다 — 그건 `ApplicationService.transition()` 몫.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import Field

from auto_apply.contracts._base import IdentifierFree
from auto_apply.domain.enums import RunKind, RunStatus


class JobKind(StrEnum):
    FILL = "fill"
    REVISE = "revise"
    SUBMIT = "submit"
    GENERATE = "generate"  # 문서 생성 (M5)
    REFLECT = "reflect"  # 가이드 반성 (M6)


# 브라우저를 쓰는 작업 — 전용 크롬 창이 하나라 동시성 1 (§A9).
BROWSER_KINDS: frozenset[JobKind] = frozenset({JobKind.FILL, JobKind.REVISE, JobKind.SUBMIT})

# 자동으로 다시 돌리지 않는 작업 — 최종 클릭이 나갔는지 모르는 채 되풀이하면 이중 제출이 된다.
NEVER_RETRY_KINDS: frozenset[JobKind] = frozenset({JobKind.SUBMIT})


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"  # 크래시·정지로 끊김. 이어서 돌 일이 있으면 새 행이 생긴다


class JobRecord(IdentifierFree):
    job_id: str
    kind: JobKind
    application_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    attempt: int = Field(default=1, ge=1)
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    # 이 시각 전에는 꺼내지 않는다 — 백오프 재시도를 행에 남겨 재기동에도 유지한다.
    run_after: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunRecord(IdentifierFree):
    """에이전트 세션 1회 (§A3 `runs`)."""

    run_id: str
    application_id: str
    kind: RunKind
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime
    finished_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    transcript_path: str | None = None


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
