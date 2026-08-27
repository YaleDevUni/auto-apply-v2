"""API 요청/응답 모델. HTTP 경계 전용 — workflow payload(contracts/dto.py)와는 다른 계층이다.

예: `application_id` 는 서버가 발급한다(idgen) — 클라이언트가 직접 workflow_id 를
고를 수 있게 하면 §2.1 의 멱등성 키 설계가 깨진다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ApplicationState, ExecutionMode


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartApplicationRequest(_Model):
    user_id: str
    job_url: str


class StartApplicationResponse(_Model):
    application_id: str
    workflow_id: str


class ApplicationView(_Model):
    """§4.1 — state 는 워크플로우 query 가 원본, history 는 DB projection."""

    application_id: str
    state: ApplicationState
    scheduled_at: datetime | None = None
    attempts: int = 0
    history: list[PersistState] = Field(default_factory=list)


class ApplicationListItem(_Model):
    """§12 웹 콘솔 목록 1행 — `telegram/_agent_tools.py::_list_applications`와 같은 job 캐시

    join 을 REST 로 옮긴 것. company/title/job_url 은 job 캐시에 없으면(오래돼 덮어써짐 등)
    비어 있을 수 있는 best-effort 필드다.
    """

    application_id: str
    state: ApplicationState
    reason: str = ""
    scheduled_at: datetime | None = None
    submitted_at: datetime | None = None
    company: str | None = None
    title: str | None = None
    job_url: str | None = None


class ApplicationListResponse(_Model):
    items: list[ApplicationListItem] = Field(default_factory=list)


class ApplyByUrlRequest(_Model):
    url: str


class ApplyByUrlResponse(_Model):
    """`apply_intake.ApplyByUrlResult`를 HTTP 경계로 그대로 옮긴다."""

    outcome: Literal["started", "duplicate", "unsupported_platform", "not_found"]
    application_id: str | None = None
    label: str | None = None
    detail: str | None = None


class PendingDecisionResponse(_Model):
    """`ApplicationWorkflow.pending_decision` query + 이력서 PDF 서명 URL. 텔레그램 승인

    메시지(§6 `DecisionRequest`)와 같은 정보를 웹 승인 카드에 보여주기 위함.
    """

    has_pending: bool
    title: str | None = None
    job_url: str | None = None  # DecisionRequest.summary
    mode: ExecutionMode | None = None
    caution_documents: list[str] = Field(default_factory=list)
    portfolio_filename: str = ""
    caution_notes: list[str] = Field(default_factory=list)
    resume_url: str | None = None  # presigned — has_pending=False 면 None


class RecipeVersionsResponse(_Model):
    """§7 GET /recipes/{platform} — RecipeSource.versions() 그대로, version 오름차순."""

    platform: str
    versions: list[AutomationRecipe]


class PromoteRecipeRequest(_Model):
    version: int
