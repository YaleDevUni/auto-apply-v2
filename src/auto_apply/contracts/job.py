"""공고 수집·매칭 워크플로우 입출력 타입.

`JobRef`(contracts/dto.py)와 겹쳐 보이지만 역할이 다르다. `JobRef`는
`ApplicationWorkflow`가 URL 하나를 알고 있을 때 쓰는 얇은 참조값이고, `JobPosting`은
`JobSource`(대량 목록 수집)가 만들어내는 원본 레코드다 — 매칭(domain/job_*.py)이
읽는 유일한 입력 형태다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.domain.enums import BlockerCode


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JobPosting(_Frozen):
    """모든 플랫폼이 이 형태로 정규화된다 (구 프로젝트 adapters/base.py 의 후신)."""

    platform: str
    platform_job_id: str
    url: str
    company: str
    title: str
    category: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_req: str | None = None
    education_req: str | None = None
    salary: str | None = None
    deadline: str | None = None  # ISO8601 날짜 또는 None(상시채용)
    posted_at: str | None = None
    description: str = ""
    image_url: str | None = None
    image_path: str | None = None  # BlobStore 키. 본문이 이미지인 공고(§ jasoseol)
    raw: dict[str, object] = Field(default_factory=dict)


class ScreeningVerdict(_Frozen):
    """축 1 — 적합도. `domain.job_screening.screen()` 의 결과."""

    verdict: Literal["pass", "excluded"]
    exclude_code: str | None = None
    exclude_label: str | None = None
    exclude_hits: list[str] = Field(default_factory=list)
    track: str | None = None
    track_label: str | None = None
    fit_score: int = 0
    score_detail: dict[str, object] = Field(default_factory=dict)


class Blocker(_Frozen):
    code: BlockerCode
    label: str
    detail: str = ""


class ApplicabilityVerdict(_Frozen):
    """축 2 — 지원가능성. `domain.job_applicability.evaluate()` 의 결과."""

    actionable: bool
    channel: Literal["platform_form", "external_ats", "image_only"]
    apply_url: str
    blockers: list[Blocker] = Field(default_factory=list)
    requires: dict[str, object] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)


class JobRecord(_Frozen):
    """저장 단위 — 공고 + 두 축 판정. `jobs`(§4)의 논리적 행 하나.

    `applicability`는 스크리닝에서 걸러진(excluded) 공고에는 없다 — 지원할 일이
    없는 공고를 판정하는 건 낭비다 (§ domain/job_applicability.py 와 같은 이유).
    """

    job: JobPosting
    screening: ScreeningVerdict
    applicability: ApplicabilityVerdict | None = None
    collected_at: datetime


class CollectJobsInput(_Frozen):
    """`JobCollectionWorkflow.run()`의 입력. Schedule 이 시작할 때마다 넘긴다."""

    platforms: list[str]


class PlatformCollectionResult(_Frozen):
    """플랫폼 하나의 수집 결과. `collect_platform_jobs` activity의 반환값."""

    platform: str
    found: int = 0
    passed: int = 0
    excluded: int = 0
    actionable: int = 0
    enrich_errors: int = 0
    error: str | None = None


class CollectJobsResult(_Frozen):
    results: list[PlatformCollectionResult] = Field(default_factory=list)
