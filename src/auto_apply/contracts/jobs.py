"""작업 큐·run 기록 DTO (§A9, §A3). 저장소 port 는 `ports/jobs.py`."""

from datetime import datetime
from enum import StrEnum

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
