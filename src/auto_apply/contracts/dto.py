"""워크플로우 입출력 타입. workflow 파일이 import 해도 안전한 유일한 데이터 계층."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.domain.enums import ApplicationState


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Application ──────────────────────────────────────────────────────────
class JobRef(_Frozen):
    job_id: str
    platform: str
    url: str
    title: str
    company: str
    description: str = ""  # fact 매칭용. 비어 있으면 전체 fact 로 fallback.


class ApplicationSummary(_Frozen):
    """`ApplicationRepository.list_recent`의 반환 항목 — 지원 건 1개의 최신 상태 스냅샷."""

    application_id: str
    state: ApplicationState
    reason: str = ""
    scheduled_at: datetime | None = None
    submitted_at: datetime | None = None


# ── Resume ───────────────────────────────────────────────────────────────
class GenerateResumeRequest(_Frozen):
    application_id: str
    user_id: str
    job: JobRef
    # 수정요청으로 재생성할 때만 채워진다. 영속 저장 안 함 — 이 호출 한 번에만 반영된다.
    feedback: str = ""


class ResumeDraft(_Frozen):
    resume_id: str
    content: dict[str, object] = Field(default_factory=dict)
    used_fact_ids: list[str] = Field(default_factory=list)


class ReviewVerdict(_Frozen):
    passed: bool
    score: float = Field(ge=0, le=1)
    issues: list[str] = Field(default_factory=list)


class RenderedPdf(_Frozen):
    blob_key: str
    bytes_written: int


class ReviewRequest(_Frozen):
    draft: ResumeDraft
    job: JobRef
    user_id: str  # reviewer 가 자기 fact 로 grounding 검증하는 데 필요 (ground_check)


# ── Persistence (projection) ─────────────────────────────────────────────
class PersistState(_Frozen):
    application_id: str
    workflow_run_id: str
    state: ApplicationState
    reason: str = ""
    scheduled_at: datetime | None = None
    submitted_at: datetime | None = None
