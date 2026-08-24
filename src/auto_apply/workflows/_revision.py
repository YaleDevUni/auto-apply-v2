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

from auto_apply.contracts.activity_defs import (
    apply_guide_patch,
    get_cached_resume,
    propose_guide_patch,
    render_pdf,
    save_cached_resume,
)
from auto_apply.contracts.dto import (
    CachedResume,
    GenerateResumeRequest,
    GuidePatchProposal,
    JobRef,
    ProposeGuidePatchRequest,
    RenderedPdf,
    ResumeDraft,
    StartApplication,
)
from auto_apply.domain.enums import ApplicationState, DecisionKind
from auto_apply.workflows.resume import ResumeWorkflow

QUEUE_AI = "ai"
_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))


class ResumeGenerationFailed(Exception):
    """child ResumeWorkflow 가 실패했다 — 호출자가 NEEDS_HUMAN 으로 마무리한다."""


def resume_workflow_id(application_id: str, round_no: int) -> str:
    """child ResumeWorkflow 의 결정론적 id. `application.py`도 같은 공식으로 계산해 텔레그램

    `resume_llm_generation` 도구가 signal 을 보낼 대상을 알아낸다(§11.2c 한도초과
    pause-and-resume) — 문자열 포맷을 두 파일에서 각자 만들면 갈라질 위험이 있어 여기 하나로 뺐다.
    """
    return f"resume-{application_id}-{round_no}"


@dataclass(frozen=True)
class GeneratedResume:
    draft: ResumeDraft
    pdf: RenderedPdf


@dataclass(frozen=True)
class GuidePatchDecision:
    """가이드 patch 제안에 대한 응답. REVISE 면 `feedback`(코멘트)로 제안을 다시 받는다."""

    kind: DecisionKind  # APPROVE | REJECT | REVISE
    feedback: str = ""


async def generate_and_render(
    cmd: StartApplication,
    job: JobRef,
    *,
    feedback: str,
    round_no: int,
    persist: Callable[[ApplicationState], Awaitable[None]],
) -> GeneratedResume:
    """child ResumeWorkflow 실행 → PDF 렌더. 최초 생성과 REVISE 재생성이 이 함수 하나를 쓴다.

    round_no==1(REVISE 없는 최초 호출)이면 먼저 캐시(§2.3 `CachedResume`)를 본다 — 같은
    application_id 로 REJECTED/EXPIRED 뒤 재지원할 때(apply_intake.py) 이미 만들어 둔
    이력서를 또 LLM 으로 새로 만들지 않기 위해서다(2026-08-24 사용자 요청). REVISE 라운드는
    사람이 명시적으로 다시 만들어 달라는 요청이라 캐시를 건너뛴다. 새로 생성했을 때는(캐시
    적중이 아닐 때) 그 결과를 캐시에 남겨 다음 재지원이 쓸 수 있게 한다 — REVISE 로 나온
    더 다듬어진 버전도 여기 덮어써 "최신" 캐시로 유지된다.

    캐시 조회는 순수 최적화라 실패해도 전체 지원을 막으면 안 된다 — 재시도까지 다 소진된
    `ActivityError`(예: 배포 직후 worker 가 아직 재시작 전이라 activity 미등록)를 캐시 미스로
    간주하고 정상 생성 경로로 흘려보낸다. 예전엔 이걸 안 잡아서 워크플로우 전체가 처리되지
    않은 예외로 죽었고, `_finish`를 못 타 DB projection 이 `generating_resume`에 영구히
    갇혀 `apply_intake._RETRYABLE_STATES` 밖이라 재지원도 막혔다(2026-08-24 실측, GC메디아이
    건).
    """
    if round_no == 1:
        try:
            cached = await workflow.execute_activity(
                get_cached_resume,
                cmd.application_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_QUICK,
            )
        except ActivityError as e:
            workflow.logger.warning(f"resume_cache.lookup_failed: {e}")
            cached = None
        if cached is not None:
            return GeneratedResume(cached.draft, cached.pdf)

    try:
        draft = await workflow.execute_child_workflow(
            ResumeWorkflow.run,
            GenerateResumeRequest(
                application_id=cmd.application_id,
                user_id=cmd.user_id,
                job=job,
                feedback=feedback,
                approval_timeout_hours=cmd.approval_timeout_hours,
            ),
            id=resume_workflow_id(cmd.application_id, round_no),
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
    await workflow.execute_activity(
        save_cached_resume,
        CachedResume(application_id=cmd.application_id, draft=draft, pdf=pdf),
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=_QUICK,
    )
    return GeneratedResume(draft, pdf)


async def revise_guide(
    cmd: StartApplication,
    job: JobRef,
    feedback: str,
    *,
    await_guide_decision: Callable[[GuidePatchProposal], Awaitable[GuidePatchDecision]],
    notify: Callable[[str, str], Awaitable[None]],
) -> None:
    """REVISE(scope=GENERAL) 일 때만 호출된다.

    사람이 diff 를 한 번 더 승인해야 config/resume_guide.md 에 반영된다(§CLAUDE.md "되돌릴 수
    없는 지점엔 사람" — 가이드는 이후 모든 생성에 영향을 주는 레버라 되돌리기 어려운 축이다).
    승인 전 코멘트로 제안 자체를 다시 받을 수 있다 — `cmd.max_guide_revisions`를 넘으면
    포기한다(무한 재제안 루프 방지, `cmd.max_revisions`와 같은 이유). 워크플로우는 설정을
    직접 안 읽으므로(결정성) 이 상한은 시작 시점에 `StartApplication`에 주입된 값을 쓴다.
    이 단계가 실패하거나 거절돼도 호출자는 계속 진행한다 — 이번 라운드 재생성엔 어차피
    `feedback`이 specific 처럼 그대로 들어가므로, 가이드 반영은 덤이지 필수 경로가 아니다.
    """
    current_feedback = feedback
    rounds = 0
    while True:
        try:
            proposal = await workflow.execute_activity(
                propose_guide_patch,
                ProposeGuidePatchRequest(user_id=cmd.user_id, job=job, feedback=current_feedback),
                start_to_close_timeout=timedelta(minutes=5),
                task_queue=QUEUE_AI,
                retry_policy=_QUICK,
            )
        except ActivityError as e:
            await notify("GUIDE_PATCH_FAILED", f"가이드 수정안 생성에 실패했다: {e}")
            return

        decision = await await_guide_decision(proposal)
        if decision.kind is DecisionKind.APPROVE:
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
            return
        if decision.kind is not DecisionKind.REVISE:
            return  # REJECT (또는 타임아웃) — 가이드는 그대로 둔다

        rounds += 1
        if rounds > cmd.max_guide_revisions:
            await notify(
                "GUIDE_PATCH_FAILED",
                f"가이드 patch 코멘트가 {cmd.max_guide_revisions}회를 넘어 포기했다",
            )
            return
        prev_patches = "\n".join(
            f"[{i}] old: {p.old or '(없음)'}\nnew: {p.new}"
            for i, p in enumerate(proposal.patches, start=1)
        )
        current_feedback = (
            f"{feedback}\n\n[이전 제안]\n{prev_patches}\n\n"
            f"[이 제안에 대한 사용자 코멘트]\n{decision.feedback}"
        )
