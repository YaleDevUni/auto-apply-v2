"""Activity 인터페이스 stub.

workflow 는 여기 있는 함수를 호출하고, Temporal 은 @activity.defn 의 '이름'으로
worker 에 등록된 실제 구현을 찾는다. 덕분에 workflow → 구현 방향 import 가 0 이 된다.

경고: 이 파일의 함수를 Worker(activities=[...]) 에 등록하면 NotImplementedError 가 난다.
"""

from temporalio import activity

from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    RenderedPdf,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)

_ONLY = "interface only — 구현은 auto_apply.activities 에 있다"


@activity.defn(name="generate_resume")
async def generate_resume(req: GenerateResumeRequest) -> ResumeDraft:
    raise NotImplementedError(_ONLY)


@activity.defn(name="review_resume")
async def review_resume(req: ReviewRequest) -> ReviewVerdict:
    raise NotImplementedError(_ONLY)


@activity.defn(name="render_pdf")
async def render_pdf(draft: ResumeDraft) -> RenderedPdf:
    raise NotImplementedError(_ONLY)


@activity.defn(name="ping")
async def ping(message: str) -> str:
    """M0 smoke test 용."""
    raise NotImplementedError(_ONLY)
