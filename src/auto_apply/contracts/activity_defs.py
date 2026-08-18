"""Activity 인터페이스 stub (ARCHITECTURE.md §11.3).

workflow 는 여기 있는 함수를 호출하고, Temporal 은 @activity.defn 의 '이름'으로
worker 에 등록된 실제 구현을 찾는다. 덕분에 workflow → 구현 방향 import 가 0 이 된다.

경고: 이 파일의 함수를 Worker(activities=[...]) 에 등록하면 NotImplementedError 가 난다.
"""

from temporalio import activity

from auto_apply.contracts.dto import (
    Eligibility,
    ExecuteInput,
    ExecutionResult,
    GenerateResumeRequest,
    JobRef,
    PersistState,
    RenderedPdf,
    ResumeDraft,
    ReviewVerdict,
)

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
async def review_resume(draft: ResumeDraft) -> ReviewVerdict:
    raise NotImplementedError(_ONLY)


@activity.defn(name="render_pdf")
async def render_pdf(draft: ResumeDraft) -> RenderedPdf:
    raise NotImplementedError(_ONLY)


@activity.defn(name="execute_application")
async def execute_application(inp: ExecuteInput) -> ExecutionResult:
    raise NotImplementedError(_ONLY)


@activity.defn(name="persist_state")
async def persist_state(state: PersistState) -> None:
    """상태를 DB 에 쓰는 유일한 통로 (§4.1). 멱등해야 한다."""
    raise NotImplementedError(_ONLY)


@activity.defn(name="ping")
async def ping(message: str) -> str:
    """M0 smoke test 용."""
    raise NotImplementedError(_ONLY)
