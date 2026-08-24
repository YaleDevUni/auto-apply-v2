"""Activity 인터페이스 stub (ARCHITECTURE.md §11.3).

workflow 는 여기 있는 함수를 호출하고, Temporal 은 @activity.defn 의 '이름'으로
worker 에 등록된 실제 구현을 찾는다. 덕분에 workflow → 구현 방향 import 가 0 이 된다.

경고: 이 파일의 함수를 Worker(activities=[...]) 에 등록하면 NotImplementedError 가 난다.
"""

from temporalio import activity

from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ApplyIntakeInput,
    ApplyIntakeResult,
    CachedResume,
    DecisionRequest,
    DecisionTicket,
    Eligibility,
    ExecuteInput,
    ExecutionResult,
    GenerateResumeRequest,
    GuidePatchProposal,
    JobRef,
    NotifyEvent,
    PersistState,
    PromoteRecipeInput,
    ProposeGuidePatchRequest,
    QuarantineRecipeInput,
    RecipeDiffResult,
    RenderedPdf,
    RepairDiagnosis,
    RepairInput,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
    VerifyInput,
    VerifyResult,
)
from auto_apply.contracts.job import PlatformCollectionResult
from auto_apply.contracts.recipe import AutomationRecipe

_ONLY = "interface only — 구현은 auto_apply.activities 에 있다"


@activity.defn(name="collect_job")
async def collect_job(job_url: str) -> JobRef:
    raise NotImplementedError(_ONLY)


@activity.defn(name="evaluate_eligibility")
async def evaluate_eligibility(job: JobRef) -> Eligibility:
    raise NotImplementedError(_ONLY)


@activity.defn(name="generate_resume")
async def generate_resume(req: GenerateResumeRequest) -> ResumeDraft:
    raise NotImplementedError(_ONLY)


@activity.defn(name="review_resume")
async def review_resume(req: ReviewRequest) -> ReviewVerdict:
    raise NotImplementedError(_ONLY)


@activity.defn(name="propose_guide_patch")
async def propose_guide_patch(req: ProposeGuidePatchRequest) -> GuidePatchProposal:
    raise NotImplementedError(_ONLY)


@activity.defn(name="apply_guide_patch")
async def apply_guide_patch(patch: GuidePatchProposal) -> None:
    raise NotImplementedError(_ONLY)


@activity.defn(name="render_pdf")
async def render_pdf(draft: ResumeDraft) -> RenderedPdf:
    raise NotImplementedError(_ONLY)


@activity.defn(name="execute_application")
async def execute_application(inp: ExecuteInput) -> ExecutionResult:
    raise NotImplementedError(_ONLY)


@activity.defn(name="request_approval")
async def request_approval(req: DecisionRequest) -> DecisionTicket:
    raise NotImplementedError(_ONLY)


@activity.defn(name="load_active_recipe")
async def load_active_recipe(platform: str) -> AutomationRecipe:
    raise NotImplementedError(_ONLY)


@activity.defn(name="verify_submission")
async def verify_submission(inp: VerifyInput) -> VerifyResult:
    raise NotImplementedError(_ONLY)


@activity.defn(name="notify")
async def notify(event: NotifyEvent) -> None:
    raise NotImplementedError(_ONLY)


@activity.defn(name="persist_state")
async def persist_state(state: PersistState) -> None:
    """상태를 DB 에 쓰는 유일한 통로 (§4.1). 멱등해야 한다."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="record_attempt")
async def record_attempt(attempt: ApplicationAttempt) -> None:
    """`application_attempts` 감사 로그를 쓰는 유일한 통로 (§4, §5). 멱등해야 한다."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="get_cached_resume")
async def get_cached_resume(application_id: str) -> CachedResume | None:
    """이력서 재사용 캐시 조회 (§2.3). 없으면 None — 정상 미스다."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="save_cached_resume")
async def save_cached_resume(resume: CachedResume) -> None:
    """이력서 재사용 캐시를 쓰는 유일한 통로 (§2.3). application_id 기준 멱등 upsert."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="delete_cached_resume")
async def delete_cached_resume(application_id: str) -> None:
    """COMPLETED/CANCELLED 로 끝나 다시는 재지원 후보가 안 되는 캐시를 지운다 (§2.3, 멱등)."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="ping")
async def ping(message: str) -> str:
    """M0 smoke test 용."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="collect_platform_jobs")
async def collect_platform_jobs(platform: str) -> PlatformCollectionResult:
    """목록 수집 → 스크리닝 → 상세 조회 → 지원가능성 판정 → 저장 (§11.2b)."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="start_actionable_applications")
async def start_actionable_applications(cmd: ApplyIntakeInput) -> ApplyIntakeResult:
    """actionable 공고 캐시 상위 N건에 `ApplicationWorkflow` 시작 (§ apply-schedule)."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="diagnose_recipe_failure")
async def diagnose_recipe_failure(req: RepairInput) -> RepairDiagnosis:
    """실패 시점 DOM 으로 "애초에 recipe 문제인가"를 판정한다 (§2.4a) — 사람에게 "진짜

    깨졌나요?"를 묻기 전의 근거 수집이다. 판정이 수선 여부를 대신 결정하지 않는다.
    """
    raise NotImplementedError(_ONLY)


@activity.defn(name="propose_recipe_diff")
async def propose_recipe_diff(req: RepairInput) -> RecipeDiffResult:
    """LLM diff 제안 (§2.4 node B). `previous`도 같이 돌려줘 워크플로우가 재조회 없이

    정책 검증(순수 함수)을 직접 부를 수 있게 한다.
    """
    raise NotImplementedError(_ONLY)


@activity.defn(name="save_recipe_candidate")
async def save_recipe_candidate(recipe: AutomationRecipe) -> AutomationRecipe:
    """샌드박스 dry-run 을 통과한 draft 를 candidate 로 이력에 추가한다 (§2.4 node G)."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="quarantine_recipe")
async def quarantine_recipe(req: QuarantineRecipeInput) -> AutomationRecipe:
    """사람이 "진짜 깨졌다"를 확정한 뒤에만 호출된다 (§2.4a) — 이 시점부터 그 플랫폼의

    지원 실행이 `load_active_recipe` 에서 막힌다.
    """
    raise NotImplementedError(_ONLY)


@activity.defn(name="promote_recipe")
async def promote_recipe(req: PromoteRecipeInput) -> AutomationRecipe:
    """사람이 승격을 승인한 뒤에만 호출된다 (§2.4 node J)."""
    raise NotImplementedError(_ONLY)
