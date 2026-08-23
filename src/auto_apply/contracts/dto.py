"""워크플로우 입출력 타입. workflow 파일이 import 해도 안전한 유일한 데이터 계층."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import (
    ApplicationState,
    AttemptOutcome,
    DecisionKind,
    ExecutionMode,
    RevisionScope,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Application ──────────────────────────────────────────────────────────
class StartApplication(_Frozen):
    # 텔레그램 callback_data 는 64바이트 한계가 있다(adapters/notifier/telegram.py). 가장
    # 빠듯한 조합("vs:{application_id}:specific:{nonce}", nonce 는 `nonce_`+16hex=22바이트
    # 고정)이 64바이트를 넘지 않으려면 application_id ≤ 29 여야 한다 — 24로 여유를 둔다.
    # API(`POST /applications`)는 idgen("app_"+16hex=20)이라 항상 안전하고, CLI `start` 의
    # 사람이 고르는 id 만 이 상한에 걸릴 수 있다(라이브로 65바이트 BUTTON_DATA_INVALID 실측).
    application_id: str = Field(max_length=24)
    user_id: str
    job_url: str
    # 워크플로우는 설정을 직접 읽지 않는다 (결정성). 시작 시점에 주입한다.
    approval_timeout_hours: int = Field(default=72, ge=1)
    dry_run_only: bool = True
    max_revisions: int = Field(default=10, ge=1)
    max_guide_revisions: int = Field(default=5, ge=1)


class ApplyIntakeInput(_Frozen):
    """`ApplyIntakeWorkflow.run()`의 입력. Schedule 이 시작할 때마다 넘긴다(§ apply-schedule)."""

    count: int = Field(default=3, ge=1)


class ApplyIntakeResult(_Frozen):
    """`apply_intake.start_actionable_applications`의 워크플로우-safe 사본. 필드는 그 함수의

    `ApplyIntakeResult` 데이터클래스와 의도적으로 동일한 모양을 유지한다.
    """

    started: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    candidates: int = 0


class ScheduleConfig(_Frozen):
    """공고 수집/자동 지원 Schedule 하나의 설정(§ apply-schedule). `.env`가 아니라 DB에

    산다(`ScheduleConfigRepository`) — 채팅으로 시각/건수를 바꾸면 재배포 없이 바로 반영되게
    하려는 선택(2026-08-22, "cron으로 하지말고 서버에서 하면 설정파일 건드릴 필요 없다"는
    사용자 요청). `count`는 target="apply"에서만, `platforms`는 target="collection"에서만 쓴다
    — 둘을 한 모델에 담은 건 두 target이 hour/minute 만큼은 완전히 같은 모양이라서다.
    """

    target: Literal["collection", "apply"]
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    count: int | None = Field(default=None, ge=1)
    platforms: list[str] | None = None


class JobRef(_Frozen):
    job_id: str
    platform: str
    url: str
    title: str
    company: str
    description: str = ""  # fact 매칭용(§2.3). 비어 있으면 전체 fact 로 fallback.


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


class ApplicationSummary(_Frozen):
    """`ApplicationRepository.list_recent`의 반환 항목 — 지원 건 1개의 최신 상태 스냅샷."""

    application_id: str
    state: ApplicationState
    reason: str = ""
    scheduled_at: datetime | None = None
    submitted_at: datetime | None = None


# ── Human-in-the-loop ────────────────────────────────────────────────────
class DecisionRequest(_Frozen):
    application_id: str
    workflow_id: str
    title: str
    summary: str
    artifact_url: str | None = None
    # True 면 이력서 가이드 patch 승인 요청이다 — Notifier 가 승인/거절 2버튼만 보여준다
    # (중첩 승인이라 그 자체를 다시 REVISE 할 순 없다).
    guide_patch: bool = False
    # 본 승인 요청(guide_patch=False)에만 채워진다 — 실제 실행될 모드를 배지로 보여주기 위함
    # (dry-run-indicator-backlog). None 이면 조회 실패로 승인 요청 시점엔 확정 못 한 것이다
    # (workflows/application.py `_peek_mode` 참고) — guide_patch 요청은 실행과 무관해 항상 None.
    mode: ExecutionMode | None = None
    # True 면 AutomationRepairWorkflow 의 recipe 승격 승인 요청이다(§2.4) — guide_patch 처럼
    # 중첩 승인이라 승인/거절 2버튼만 보여준다(REVISE 는 없다). 이 경우 `application_id`는
    # 실제 지원 건이 아니라 `f"{platform}-{form_hash}"`를 담는다 — 그래야
    # `wf_id = f"repair-{application_id}"`로 그대로 워크플로우 id 를 복원할 수 있다
    # (telegram/bridge.py).
    repair_promotion: bool = False
    # True 면 SUPERVISED 실행 중 페이지 경계 체크포인트 승인 요청이다
    # (§ supervised-checkpoint-design) — guide_patch/repair_promotion 처럼 승인/거절 2버튼만.
    # `artifact_url`엔 체크포인트 스크린샷의 blob 키가 실린다.
    checkpoint: bool = False
    # 아래 셋은 본 승인 요청(guide_patch=False 등 중첩 승인이 아닌 경우)에만 채워진다 —
    # 승인 버튼을 누르기 전 정보 비대칭을 줄이려는 목적(§ mode 와 같은 동기,
    # wanted-application-caution-indicators-backlog).
    # 자동화가 못 채우는 첨부서류 힌트(domain/job_applicability.caution_documents) — 사람이
    # 따로 준비/제출해야 한다.
    caution_documents: list[str] = Field(default_factory=list)
    # 이번 지원에 실제로 첨부될 포트폴리오 파일명(비어 있으면 첨부 없음). 이미
    # adapters/resume/_assemble.py 가 계산해 ResumeDraft.content 에 실어둔 값을 그대로 옮긴다.
    portfolio_filename: str = ""
    # LLM 이 이 공고/이 지원 건에 대해 주관적으로 판단한 주의사항(ai/schemas.ResumeContentSchema
    # 의 caution_notes) — 근거 fact_id 가 필요한 서술이 아니라 공고 본문에 대한 메타 코멘트라
    # ground_check 검증 대상이 아니다.
    caution_notes: list[str] = Field(default_factory=list)


class DecisionTicket(_Frozen):
    """Notifier 가 발급. nonce 로 버튼 재사용을 막는다 (§6)."""

    ticket_id: str
    nonce: str


class PendingDecisionView(_Frozen):
    """워크플로우 query 응답. 텔레그램 채팅 에이전트의 `resend_pending_decision` 도구가 쓴다

    (telegram/agent.py). nonce 는 워크플로우 밖으로 나가는 유일한 통로가 이 query 다 — signal
    검증(§6)과 달리 여기선 그냥 읽어서 기존 버튼을 다시 보내는 데만 쓴다. `request`는 애초에
    `request_approval` activity 로 보냈던 그 `DecisionRequest`를 그대로 담는다 —
    application_id(해시) 한 줄만 다시 보내던 걸(2026-08-23 최초 시도) 사람이 지적해, 재전송이
    회사/직무/공고 링크/PDF/주의사항까지 원래 메시지와 동일하게 보이도록 바꿨다.
    """

    has_pending: bool
    nonce: str = ""
    request: DecisionRequest | None = None


class NotifyEvent(_Frozen):
    kind: str
    application_id: str | None = None
    message: str = ""


# ── Resume ───────────────────────────────────────────────────────────────
class GenerateResumeRequest(_Frozen):
    application_id: str
    user_id: str
    job: JobRef
    # REVISE(SPECIFIC) 로 재생성할 때만 채워진다. 영속 저장 안 함 — 이 호출 한 번에만 반영된다.
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


class ResumeAttachment(_Frozen):
    """플랫폼 계정에 이미 업로드돼 있는 이력서/포트폴리오 파일 1개.

    `AttachmentManager` port (§ wanted-resume-list-cleanup-backlog) 가 다루는 단위 —
    우리 쪽 blob store 키가 아니라 플랫폼이 매긴 키(wanted 는 `key`)다.
    """

    key: str
    title: str
    content_type: str
    updated_at: datetime


# ── Resume guide patch (REVISE/general, domain/guide_patch.py) ────────────
class ProposeGuidePatchRequest(_Frozen):
    user_id: str
    job: JobRef
    feedback: str


class GuidePatchItem(_Frozen):
    """치환 쌍 하나 — ai/schemas.GuidePatchItem 을 activities/guide.py 가 그대로 옮겨 담는다."""

    old: str
    new: str
    rationale: str = ""


class GuidePatchProposal(_Frozen):
    """LLM 출력 + platform. 전문이 아니라 치환 쌍 목록만 — apply_guide_patch 가 각 patch 를

    순서대로 적용하며, 하나라도 정확히 1번 매치되지 않으면 그 자리에서 실패한다(전체 patch
    가 원자적으로 적용되진 않는다 — GuidePatchNotFound/Ambiguous 는 재시도 없이 사람에게
    넘기는 실패라 부분 적용 상태를 그대로 노출해도 된다는 판단). 한 번의 REVISE(general)
    피드백에 서로 다른 지시가 여러 개 섞여 있을 수 있어 리스트다(메모리
    resume-revise-feedback-design). `platform`은 어느 `resume_guide.{platform}.md`에 적용할지를
    propose 에서 apply 까지 activity 경계를 넘어 들고 가는 값이다(활동 인자는 하나뿐이라 여기
    실어야 한다).
    """

    patches: list[GuidePatchItem]
    platform: str = ""


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


# ── Repair (§2.4) ────────────────────────────────────────────────────────
class RepairInput(_Frozen):
    platform: str
    form_hash: str
    snapshot_key: str
    failed_version: int
    # 샌드박스 dry-run 이 실제로 값을 채워 넣을 수 있어야 selector 수정이 진짜 통하는지
    # 검증된다 — 이 필드가 없으면 실행 계층까지 안 가고 "그럴듯한 diff"만 만드는 셈이라
    # `AutomationRepairWorkflow`를 부르는 쪽(그 시점의 실패한 지원)이 자신의 컨텍스트를
    # 그대로 넘긴다. dedupe(동시에 같은 폼이 실패한 다른 지원)로 다른 실행 컨텍스트가
    # 있었어도 먼저 도착한 실행의 컨텍스트로 검증한다 — 셀렉터가 맞는지는 데이터와 무관하다.
    ctx: ExecutionContext
    # `activity_failure()`가 만든 실패 사유 문자열 그대로(§2.4 node B 프롬프트의 [실패 사유]).
    # 이게 없으면 LLM이 왜 실패했는지 전혀 모른 채 diff 를 낸다 — 실제로 propose_recipe_diff 가
    # 이 필드 없이 form_hash 를 대신 프롬프트에 넣던 버그가 timeout 실패를 셀렉터 문제로 오진하게
    # 만든 사고로 이어졌다(메모리 wanted-goto-timeout-misdiagnosed-as-recipe-bug).
    failure_reason: str = ""
    # 실패한 action 의 recipe.actions 인덱스 (RecipeExecutionError.failed_action_index).
    # propose_recipe_diff 가 "이 실패가 goto 액션의 timeout 인가"를 결정론적으로 판정할 때만
    # 쓴다(domain/recipe_repair.bump_goto_timeout) — 없으면 그 판정을 건너뛰고 LLM 경로로 간다.
    failed_action_index: int | None = None


class RecipeDiffResult(_Frozen):
    """propose_recipe_diff activity 의 반환값. `previous`도 같이 돌려줘야 워크플로우가

    (§11.3 "모든 I/O는 activity 안에서만") 다시 조회하지 않고 순수 함수
    `domain.recipe_policy.check_recipe_policy(candidate, previous=...)`를 직접 부를 수 있다.
    """

    candidate: AutomationRecipe
    previous: AutomationRecipe


class PromoteRecipeInput(_Frozen):
    platform: str
    version: int


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
    # kind=REVISE 일 때만 쓰인다.
    feedback: str = ""
    scope: RevisionScope | None = None


# ── Signals ──────────────────────────────────────────────────────────────
class ApproveSignal(_Frozen):
    scheduled_at: datetime | None = None
    decided_by: str = ""
    nonce: str = ""


class RejectSignal(_Frozen):
    reason: str = ""
    decided_by: str = ""
    nonce: str = ""


class ReviseSignal(_Frozen):
    """텔레그램 3번째 갈래. `scope`가 저장 위치를 가른다 (domain/enums.RevisionScope)."""

    feedback: str
    scope: RevisionScope
    decided_by: str = ""
    nonce: str = ""


class GuidePatchDecisionSignal(_Frozen):
    """가이드 patch 제안에 대한 2차 승인. 본 승인/거절 signal 과 nonce 슬롯이 분리돼 있다."""

    decided_by: str = ""
    nonce: str = ""


class GuidePatchReviseSignal(_Frozen):
    """가이드 patch 제안에 대한 코멘트 재요청. approve/reject_guide_patch 와 nonce 슬롯을 공유."""

    feedback: str
    decided_by: str = ""
    nonce: str = ""


class RescheduleSignal(_Frozen):
    scheduled_at: datetime


# ── Resume review / verify ───────────────────────────────────────────────
class ReviewRequest(_Frozen):
    draft: ResumeDraft
    job: JobRef
    user_id: str  # reviewer 가 자기 fact 로 grounding 검증하는 데 필요(§2.3 ground_check)


class VerifyInput(_Frozen):
    application_id: str
    platform: str
    # 플랫폼이 실제 제출 여부를 조회하는 데 필요한 부가 정보. 둘 다 없어도 구조는 유효하지만
    # (기존 어댑터/테스트와의 호환), 실제 조회를 하는 어댑터(wanted 등)는 이게 없으면 확인을
    # 포기하고 unverified 로 안전하게 떨어뜨린다(§5 부분 제출 위험 방어).
    job_id: str = ""  # JobRef.job_id (플랫폼 접두어 포함, 예: "wanted:380443")
    since: datetime | None = None  # 이 시각 이후 생성된 기록만 "이번 시도의 제출"로 인정한다
    # — 과거에 같은 공고에 지원한 이력이 있으면 그 기록으로 오탐(false verified)할 수 있다.


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
