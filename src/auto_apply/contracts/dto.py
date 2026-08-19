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
    # 워크플로우는 설정을 직접 읽지 않는다 (결정성). 시작 시점에 주입한다.
    approval_timeout_hours: int = Field(default=72, ge=1)
    dry_run_only: bool = True


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


# ── Signals ──────────────────────────────────────────────────────────────
class ApproveSignal(_Frozen):
    scheduled_at: datetime | None = None
    decided_by: str = ""
    nonce: str = ""


class RejectSignal(_Frozen):
    reason: str = ""
    decided_by: str = ""
    nonce: str = ""


class RescheduleSignal(_Frozen):
    scheduled_at: datetime


# ── Resume review / verify ───────────────────────────────────────────────
class ReviewRequest(_Frozen):
    draft: ResumeDraft
    job: JobRef


class VerifyInput(_Frozen):
    application_id: str
    platform: str


class VerifyResult(_Frozen):
    verified: bool
    detail: str = ""


# ── Attempt audit log (§4, §5) ───────────────────────────────────────────
class ApplicationAttempt(_Frozen):
    """`application_attempts` 1행. 실행 1회 = 1행 (§4).

    submit 직전에 outcome=UNKNOWN("submitting")으로 먼저 기록하고, 결과가 나오면 같은
    (application_id, attempt) 를 덮어쓴다. 그래야 부분 제출 중 크래시가 나도 "아직 결론이
    안 난 시도가 있었다"는 사실이 감사 로그에 남는다 — 재개 시 verify_submission 을 먼저
    돌려야 하는 이유가 바로 이 UNKNOWN 상태다.
    """

    application_id: str
    attempt: int
    recipe_platform: str
    recipe_version: int
    mode: ExecutionMode
    outcome: AttemptOutcome
    started_at: datetime
    ended_at: datetime | None = None
    submitted_at: datetime | None = None
    error_code: str = ""
    snapshot_key: str = ""
    artifact_keys: list[str] = Field(default_factory=list)
    detail: str = ""
