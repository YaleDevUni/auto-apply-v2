"""계층 사이를 오가는 DTO — 벤더 의존 없는 순수 데이터 계층 (§A2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.domain.enums import ApplicationState, SubmitMode


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
    at: datetime
    submitted_at: datetime | None = None


class ApplicationRecord(_Frozen):
    """지원 건 자체(상태 말고). 생성 때 한 번 정해지고 바뀌지 않는다."""

    application_id: str
    url: str
    domain: str  # 도메인 가이드(§A8)·중복 지원 조회 키. url 의 호스트, 소문자
    # 생성 시점 스냅샷 — 설정이 나중에 live 로 바뀌어도 이미 만든 건은 조용히 따라가지
    # 않는다(절대 규칙 2).
    submit_mode: SubmitMode = SubmitMode.DRY_RUN


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
    """상태 전이 1건 (§A3 이력 1행). `ApplicationService.transition()` 만 만든다."""

    application_id: str
    # 전이를 일으킨 run. 사람 조작(trigger·approve·cancel)은 run 이 없어 None.
    run_id: str | None
    state: ApplicationState
    reason: str = ""
    at: datetime
    submitted_at: datetime | None = None
