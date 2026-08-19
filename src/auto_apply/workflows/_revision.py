"""ApplicationWorkflow 의 REVISE(수정요청) 루프 헬퍼 (ARCHITECTURE.md §2.2 확장).

`application.py`가 200줄을 넘기지 않도록 이 구간만 분리했다 — `_execution.py`와 같은 이유.
signal 상태(nonce·decision)는 workflow 인스턴스에만 있을 수 있어 `application.py`에 남기고,
여기는 그 상태를 건드리지 않는 순수 workflow 코드(contracts/domain 만 의존)만 둔다.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError

from auto_apply.contracts.activity_defs import apply_guide_patch, propose_guide_patch, render_pdf
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    GuidePatchProposal,
    JobRef,
    ProposeGuidePatchRequest,
    RenderedPdf,
    ResumeDraft,
    StartApplication,
)
from auto_apply.domain.enums import ApplicationState
from auto_apply.workflows.resume import ResumeWorkflow

QUEUE_AI = "ai"
MAX_REVISIONS = 3  # 이 라운드를 넘으면 사람에게 넘긴다 — 무한 재생성 루프를 만들지 않는다
_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))


class ResumeGenerationFailed(Exception):
    """child ResumeWorkflow 가 실패했다 — 호출자가 NEEDS_HUMAN 으로 마무리한다."""


@dataclass(frozen=True)
class GeneratedResume:
    draft: ResumeDraft
    pdf: RenderedPdf


async def generate_and_render(
    cmd: StartApplication,
    job: JobRef,
    *,
    feedback: str,
    round_no: int,
    persist: Callable[[ApplicationState], Awaitable[None]],
) -> GeneratedResume:
    """child ResumeWorkflow 실행 → PDF 렌더. 최초 생성과 REVISE 재생성이 이 함수 하나를 쓴다."""
    try:
        draft = await workflow.execute_child_workflow(
            ResumeWorkflow.run,
            GenerateResumeRequest(
                application_id=cmd.application_id,
                user_id=cmd.user_id,
                job=job,
                feedback=feedback,
            ),
            id=f"resume-{cmd.application_id}-{round_no}",
            task_queue=QUEUE_AI,
        )
    except ChildWorkflowError as e:
        raise ResumeGenerationFailed(str(e)) from e

    await persist(ApplicationState.RENDERING_PDF)
    pdf = await workflow.execute_activity(
        render_pdf,
        draft,
        start_to_close_timeout=timedelta(minutes=5),
        task_queue=QUEUE_AI,
        retry_policy=_QUICK,
    )
    return GeneratedResume(draft, pdf)


async def revise_guide(
    cmd: StartApplication,
    job: JobRef,
    feedback: str,
    *,
    await_guide_decision: Callable[[GuidePatchProposal], Awaitable[bool]],
    notify: Callable[[str, str], Awaitable[None]],
) -> None:
    """REVISE(scope=GENERAL) 일 때만 호출된다.

    사람이 diff 를 한 번 더 승인해야 config/resume_guide.md 에 반영된다(§CLAUDE.md "되돌릴 수
    없는 지점엔 사람" — 가이드는 이후 모든 생성에 영향을 주는 레버라 되돌리기 어려운 축이다).
    이 단계가 실패하거나 거절돼도 呼출자는 계속 진행한다 — 이번 라운드 재생성엔 어차피
    `feedback`이 specific 처럼 그대로 들어가므로, 가이드 반영은 덤이지 필수 경로가 아니다.
    """
    try:
        proposal = await workflow.execute_activity(
            propose_guide_patch,
            ProposeGuidePatchRequest(user_id=cmd.user_id, job=job, feedback=feedback),
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=QUEUE_AI,
            retry_policy=_QUICK,
        )
    except ActivityError as e:
        await notify("GUIDE_PATCH_FAILED", f"가이드 수정안 생성에 실패했다: {e}")
        return

    if not await await_guide_decision(proposal):
        return

    try:
        await workflow.execute_activity(
            apply_guide_patch,
            proposal,
            start_to_close_timeout=timedelta(minutes=1),
            task_queue=QUEUE_AI,
            retry_policy=_QUICK,
        )
    except ActivityError as e:
        await notify("GUIDE_PATCH_FAILED", f"가이드 반영에 실패했다: {e}")
