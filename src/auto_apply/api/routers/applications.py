"""§7 지원 흐름 엔드포인트.

규칙: 여기서 LLM/Playwright 를 실행하지 않는다. 워크플로우를 시작하거나 signal 을
보내고 즉시 응답한다. workflow_id = f"application-{id}" 가 멱등성 키다 (§2.1).
"""

from fastapi import APIRouter, HTTPException
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from auto_apply.api.deps import ContainerDep, TemporalClientDep
from auto_apply.api.schemas import (
    ApplicationView,
    StartApplicationRequest,
    StartApplicationResponse,
)
from auto_apply.contracts.dto import (
    ApproveSignal,
    RejectSignal,
    RescheduleSignal,
    ReviseSignal,
    StartApplication,
)
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow

router = APIRouter(prefix="/applications", tags=["applications"])


def _wf_id(application_id: str) -> str:
    return f"application-{application_id}"


def _not_found(e: RPCError, application_id: str) -> HTTPException:
    if e.status == RPCStatusCode.NOT_FOUND:
        return HTTPException(404, f"application {application_id} not found")
    return HTTPException(502, f"temporal error: {e.message}")


@router.post("", status_code=202)
async def start_application(
    req: StartApplicationRequest, c: ContainerDep, client: TemporalClientDep
) -> StartApplicationResponse:
    application_id = c.idgen.new_id("app")
    wf_id = _wf_id(application_id)
    await client.start_workflow(
        ApplicationWorkflow.run,
        StartApplication(
            application_id=application_id,
            user_id=req.user_id,
            job_url=req.job_url,
            approval_timeout_hours=c.settings.approval_timeout_hours,
            dry_run_only=c.settings.dry_run_only,
            max_revisions=c.settings.max_revisions,
            max_guide_revisions=c.settings.max_guide_revisions,
        ),
        id=wf_id,  # 같은 application_id 로 두 번 호출돼도 REJECT_DUPLICATE 가 막는다
        task_queue=QUEUE_DEFAULT,
    )
    return StartApplicationResponse(application_id=application_id, workflow_id=wf_id)


@router.get("/{application_id}")
async def get_application(
    application_id: str, c: ContainerDep, client: TemporalClientDep
) -> ApplicationView:
    """상태는 워크플로우 query 가 원본, history 는 DB projection 을 곁들인다 (§4.1)."""
    try:
        view = await client.get_workflow_handle(_wf_id(application_id)).query(
            ApplicationWorkflow.state
        )
    except RPCError as e:
        raise _not_found(e, application_id) from e
    async with c.uow() as uow:
        history = await uow.applications.history(application_id)
    return ApplicationView(
        application_id=application_id,
        state=view.state,
        scheduled_at=view.scheduled_at,
        attempts=view.attempts,
        history=history,
    )


@router.post("/{application_id}/approve", status_code=202)
async def approve_application(
    application_id: str, sig: ApproveSignal, client: TemporalClientDep
) -> None:
    await _signal_approve(client, application_id, sig)


@router.post("/{application_id}/reject", status_code=202)
async def reject_application(
    application_id: str, sig: RejectSignal, client: TemporalClientDep
) -> None:
    await _signal_reject(client, application_id, sig)


@router.post("/{application_id}/revise", status_code=202)
async def revise_application(
    application_id: str, sig: ReviseSignal, client: TemporalClientDep
) -> None:
    """텔레그램 없이(콘솔/테스트) REVISE 를 트리거하는 경로 — approve/reject 와 같은 모양."""
    try:
        await client.get_workflow_handle(_wf_id(application_id)).signal(
            ApplicationWorkflow.revise, sig
        )
    except RPCError as e:
        raise _not_found(e, application_id) from e


@router.post("/{application_id}/schedule", status_code=202)
async def reschedule_application(
    application_id: str, sig: RescheduleSignal, client: TemporalClientDep
) -> None:
    await _signal_reschedule(client, application_id, sig)


@router.post("/{application_id}/cancel", status_code=202)
async def cancel_application(application_id: str, client: TemporalClientDep) -> None:
    try:
        await client.get_workflow_handle(_wf_id(application_id)).signal(ApplicationWorkflow.cancel)
    except RPCError as e:
        raise _not_found(e, application_id) from e


async def _signal_approve(client: Client, application_id: str, sig: ApproveSignal) -> None:
    try:
        await client.get_workflow_handle(_wf_id(application_id)).signal(
            ApplicationWorkflow.approve, sig
        )
    except RPCError as e:
        raise _not_found(e, application_id) from e


async def _signal_reject(client: Client, application_id: str, sig: RejectSignal) -> None:
    try:
        await client.get_workflow_handle(_wf_id(application_id)).signal(
            ApplicationWorkflow.reject, sig
        )
    except RPCError as e:
        raise _not_found(e, application_id) from e


async def _signal_reschedule(client: Client, application_id: str, sig: RescheduleSignal) -> None:
    try:
        await client.get_workflow_handle(_wf_id(application_id)).signal(
            ApplicationWorkflow.reschedule, sig
        )
    except RPCError as e:
        raise _not_found(e, application_id) from e
