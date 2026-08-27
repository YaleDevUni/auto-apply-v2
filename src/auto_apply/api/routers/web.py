"""§12 웹 콘솔 전용 엔드포인트. `applications.py`(§7)를 더 키우지 않으려고 분리했다.

여기도 §7 규칙 그대로 — LLM/Playwright 를 실행하지 않는다. `apply_intake.apply_by_url`을
그대로 호출하거나, 워크플로우 query 결과를 HTTP 응답 모양으로 옮기기만 한다.
"""

from fastapi import APIRouter, HTTPException, Request, Response
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from auto_apply.api.deps import ContainerDep, TemporalClientDep
from auto_apply.api.schemas import (
    ApplicationListItem,
    ApplicationListResponse,
    ApplyByUrlRequest,
    ApplyByUrlResponse,
    PendingDecisionResponse,
)
from auto_apply.apply_intake import apply_by_url
from auto_apply.contracts.dto import PendingDecisionView
from auto_apply.domain.errors import BlobNotFound
from auto_apply.domain.job_identity import canonical_key
from auto_apply.workflows.application import ApplicationWorkflow

router = APIRouter(prefix="/applications", tags=["web"])


async def _pending_view(application_id: str, client: Client) -> PendingDecisionView:
    try:
        return await client.get_workflow_handle(f"application-{application_id}").query(
            ApplicationWorkflow.pending_decision
        )
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, f"application {application_id} not found") from e
        raise HTTPException(502, f"temporal error: {e.message}") from e


@router.get("")
async def list_applications(c: ContainerDep, limit: int = 50) -> ApplicationListResponse:
    """`telegram/_agent_tools.py::_list_applications`와 같은 job 캐시 join(best-effort)을

    REST 로 옮긴 것 — "지원 중"/"히스토리"를 한 목록으로 주고 프론트가 `state`로 나눈다.
    """
    async with c.uow() as uow:
        summaries = await uow.applications.list_recent(limit=limit)
        jobs_by_id = {
            canonical_key(r.job.company, r.job.title): r.job for r in await uow.jobs.actionable()
        }
    items = []
    for s in summaries:
        job = jobs_by_id.get(s.application_id)
        items.append(
            ApplicationListItem(
                application_id=s.application_id,
                state=s.state,
                reason=s.reason,
                scheduled_at=s.scheduled_at,
                submitted_at=s.submitted_at,
                company=job.company if job else None,
                title=job.title if job else None,
                job_url=job.url if job else None,
            )
        )
    return ApplicationListResponse(items=items)


@router.post("/apply-by-url", status_code=202)
async def apply_by_url_endpoint(
    req: ApplyByUrlRequest, c: ContainerDep, client: TemporalClientDep
) -> ApplyByUrlResponse:
    result = await apply_by_url(req.url, c, client)
    return ApplyByUrlResponse(
        outcome=result.outcome,
        application_id=result.application_id,
        label=result.label,
        detail=result.detail,
    )


@router.get("/{application_id}/pending")
async def get_pending_decision(
    application_id: str, request: Request, client: TemporalClientDep
) -> PendingDecisionResponse:
    """승인 카드 + 이력서 PDF 뷰어가 쓰는 엔드포인트. 텔레그램 승인 메시지(§6)와 같은 정보다."""
    view = await _pending_view(application_id, client)
    if not view.has_pending or view.request is None:
        return PendingDecisionResponse(has_pending=False)
    req = view.request
    # presigned URL을 안 쓰는 이유는 get_resume_pdf 의 docstring 참고.
    resume_url = (
        f"{request.base_url}applications/{application_id}/resume.pdf" if req.artifact_url else None
    )
    return PendingDecisionResponse(
        has_pending=True,
        title=req.title,
        job_url=req.summary,
        mode=req.mode,
        caution_documents=req.caution_documents,
        portfolio_filename=req.portfolio_filename,
        caution_notes=req.caution_notes,
        resume_url=resume_url,
    )


@router.get("/{application_id}/resume.pdf")
async def get_resume_pdf(
    application_id: str, c: ContainerDep, client: TemporalClientDep
) -> Response:
    """PDF 를 API 가 직접 바이트로 읽어 응답한다 — 프론트에 presigned URL 을 주지 않는 이유는

    로컬 개발 스토리지(LocalBlobStore/InMemoryBlobStore)의 `presign()`이 `file://`/`memory://`를
    반환하는데, 브라우저가 그런 스킴은 iframe 으로 로드를 거부하기 때문("Not allowed to load
    local resource", 2026-08-27 실측). S3 백엔드까지 이 경로로 통일해 스토리지 구현체와
    무관하게 동작하게 한다.
    """
    view = await _pending_view(application_id, client)
    if not view.has_pending or view.request is None or view.request.artifact_url is None:
        raise HTTPException(404, "resume not found")
    try:
        data = await c.store.get(view.request.artifact_url)
    except BlobNotFound as e:
        raise HTTPException(404, "resume blob not found") from e
    return Response(content=data, media_type="application/pdf")
