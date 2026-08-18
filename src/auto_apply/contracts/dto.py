"""워크플로우 입출력 타입. workflow 파일이 import 해도 안전한 유일한 데이터 계층."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import (
    ApplicationState,
    AttemptOutcome,
    DecisionKind,
    ExecutionMode,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Application ──────────────────────────────────────────────────────────
class StartApplication(_Frozen):
    application_id: str
    user_id: str
    job_url: str


class JobRef(_Frozen):
    job_id: str
    platform: str
    url: str
    title: str
    company: str


class Eligibility(_Frozen):
    eligible: bool
    reason: str = ""


class ApplicationResult(_Frozen):
    state: ApplicationState
    reason: str = ""
    submitted_at: datetime | None = None


class StateView(_Frozen):
    """워크플로우 query 응답. /status 가 DB 대신 이걸 읽는다 (§4.1)."""

    state: ApplicationState
    scheduled_at: datetime | None = None
    attempts: int = 0


# ── Human-in-the-loop ────────────────────────────────────────────────────
class DecisionRequest(_Frozen):
    application_id: str
    workflow_id: str
    title: str
    summary: str
    artifact_url: str | None = None


class DecisionTicket(_Frozen):
    """Notifier 가 발급. nonce 로 버튼 재사용을 막는다 (§6)."""

    ticket_id: str
    nonce: str


class NotifyEvent(_Frozen):
    kind: str
    application_id: str | None = None
    message: str = ""


# ── Resume ───────────────────────────────────────────────────────────────
class GenerateResumeRequest(_Frozen):
    application_id: str
    user_id: str
    job: JobRef


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


# ── Execution ────────────────────────────────────────────────────────────
class ExecutionContext(_Frozen):
    application_id: str
    attempt: int
    profile: dict[str, str] = Field(default_factory=dict)
    upload_keys: dict[str, str] = Field(default_factory=dict)


class ExecuteInput(_Frozen):
    recipe: AutomationRecipe
    ctx: ExecutionContext
    mode: ExecutionMode


class ExecutionResult(_Frozen):
    outcome: AttemptOutcome
    submitted_at: datetime | None = None
    artifact_keys: list[str] = Field(default_factory=list)
    detail: str = ""


# ── Repair ───────────────────────────────────────────────────────────────
class RepairInput(_Frozen):
    platform: str
    form_hash: str
    snapshot_key: str
    failed_version: int


class RepairResult(_Frozen):
    promoted: bool
    new_version: int | None = None
    reason: str = ""


# ── Persistence (projection) ─────────────────────────────────────────────
class PersistState(_Frozen):
    application_id: str
    workflow_run_id: str
    state: ApplicationState
    reason: str = ""
    scheduled_at: datetime | None = None
    submitted_at: datetime | None = None


class Decision(_Frozen):
    kind: DecisionKind
    scheduled_at: datetime | None = None
    reason: str = ""
