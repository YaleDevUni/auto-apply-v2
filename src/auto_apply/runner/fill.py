"""fill run 핸들러 — QUEUED 지원 건을 에이전트가 채워 승인 대기까지 (§A6, §A3).

흐름: run 기록 시작 → FILLING → 시스템 프롬프트 조립 → 런타임이 BrowserToolbox 도구로 채움 →
도구 결과로 상태 전이(`fill_session.decide`). 끝난 모양(검토·실패·사람 대기·INCIDENT·한도)은 이
핸들러가 직접 전이하고 job 은 DONE 이다. 예외(브라우저 기동 실패·런타임 장애 등 인프라)만 run 을
FAILED 로 닫은 뒤 다시 던져 JobRunner 의 재시도 정책(§A9)에 맡긴다.
job payload 에 `resume_from` 이 있으면 재진입 run 이다(`fill_reentry`, D8) — 직전 기록과 받은 답을
프롬프트에 싣는다.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence

import structlog

from auto_apply.ai.fill_prompt import build_fill_system_prompt
from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentOutcome
from auto_apply.contracts.dto import ApplicationRecord
from auto_apply.contracts.fill_log import FillSource
from auto_apply.contracts.jobs import JobRecord, RunRecord
from auto_apply.contracts.knowledge import Answer
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState, RunKind, RunStatus
from auto_apply.domain.errors import (
    InvalidInput,
    InvalidTransition,
    ProfileNotFound,
    SubmitIncident,
)
from auto_apply.ports.agent import AgentRuntime
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.profile import ProfileSource
from auto_apply.ports.repository import UnitOfWork
from auto_apply.runner.fill_reentry import AnswerBook, HeldAnswers, load_resume, resume_request
from auto_apply.runner.fill_session import FillSession, decide
from auto_apply.runner.job_runner import describe_error
from auto_apply.services.application import ApplicationService
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.browser_toolbox_specs import agent_tools
from auto_apply.services.profile import DEFAULT_USER_ID
from auto_apply.services.run_artifacts import RunArtifacts

log = structlog.get_logger(__name__)

# (지원 건, run_id, 가린 답) → 그 run 의 도구 상자. bootstrap 이 브라우저·드라이버·사람을 묶는다
ToolboxFactory = Callable[[ApplicationRecord, str, Mapping[str, str]], BrowserToolbox]
# 런타임이 스스로 한도를 지키지 못할 때(멈춘 프로세스) 호출자가 끊는 여유
HARD_TIMEOUT_GRACE_S = 30.0


class FillRunHandler:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        applications: ApplicationService,
        artifacts: RunArtifacts,
        runtime: AgentRuntime,
        toolbox: ToolboxFactory,
        profiles: ProfileSource,
        clock: Clock,
        idgen: IdGen,
        *,
        limits: AgentLimits | None = None,
        answers: AnswerBook | None = None,
        held: HeldAnswers | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self._uow, self._apps, self._artifacts = uow, applications, artifacts
        self._runtime, self._toolbox, self._profiles = runtime, toolbox, profiles
        self._clock, self._idgen = clock, idgen
        self._limits = limits or AgentLimits()
        self._answers, self._held = answers, held or HeldAnswers()
        self._user_id = user_id

    async def __call__(self, job: JobRecord) -> None:
        if job.application_id is None:
            raise InvalidInput("fill job 에 지원 건이 없다")
        record = await self._apps.get(job.application_id)
        resume = resume_request(job.payload)
        run_id = self._idgen.new_id("run")
        ctx = {"application_id": record.application_id, "run_id": run_id}
        await self._start_run(record.application_id, run_id)
        try:
            await self._apps.transition(
                record.application_id, ApplicationState.FILLING, run_id=run_id, reason="fill run"
            )
            held = self._held.get(record.application_id)
            toolbox = self._toolbox(record, run_id, held)
            session, outcome = await self._drive(record, run_id, toolbox, resume, held)
        except Exception as exc:
            await self._finish_run(run_id, RunStatus.FAILED, error=describe_error(exc))
            raise
        verdict = decide(toolbox, session, outcome)
        if verdict.state in (ApplicationState.NEEDS_INPUT, ApplicationState.NEEDS_LOGIN):
            self._held.keep(record.application_id, toolbox.hidden_answers)  # 재진입 run 몫
        else:
            self._held.drop(record.application_id)
        try:
            # 부분 기록도 남긴다 — 재진입 run 이 여기서 이어간다(D8)
            await self._artifacts.save_fill_log(run_id, toolbox.fill_log)
            if toolbox.review is not None:
                await self._artifacts.save_review(run_id, toolbox.review)
            if toolbox.needs_human is not None:  # 답이 안 온 질문 — 사람이 나중에 답한다
                await self._artifacts.save_human_task(run_id, toolbox.needs_human)
        except Exception as exc:
            await self._finish_run(run_id, RunStatus.FAILED, error=describe_error(exc))
            if verdict.state is ApplicationState.INCIDENT:
                # 기록을 못 남겨도 제출 흔적은 숨기지 않는다 — 러너가 INCIDENT 로 둔다(§A9)
                raise SubmitIncident(verdict.reason) from exc
            raise
        log.info("fill.finished", to_state=str(verdict.state), reason=verdict.reason, **ctx)
        try:
            await self._apps.transition(
                record.application_id, verdict.state, run_id=run_id, reason=verdict.reason
            )
        except InvalidTransition as exc:
            # 그 사이 사람이 취소하는 등 다른 쪽이 먼저 바꿨다 — 그쪽이 이긴다(러너가 CONFLICT)
            await self._finish_run(run_id, RunStatus.FAILED, error=describe_error(exc))
            raise
        await self._finish_run(
            run_id,
            verdict.run_status,
            result=str(verdict.state),
            error=None if verdict.run_status is RunStatus.DONE else verdict.reason,
            outcome=outcome,
        )

    async def _drive(
        self,
        record: ApplicationRecord,
        run_id: str,
        toolbox: BrowserToolbox,
        resume: tuple[str, FillSource] | None,
        held: Mapping[str, str],
    ) -> tuple[FillSession, AgentOutcome]:
        loop = asyncio.get_running_loop()
        session = FillSession(toolbox, self._limits, loop.time)
        answers = await self._kb()
        prompt = build_fill_system_prompt(
            url=record.url,
            domain=record.domain,
            profile=await self._profile(),
            # 가이드 저장소(§A8)는 M6 — 그때까지 자리만 비워 둔다
            answers=answers,
            resume=None if resume is None else await load_resume(
                self._artifacts, resume, answers, held
            ),
        )  # fmt: skip
        try:
            async with asyncio.timeout(self._limits.max_seconds + HARD_TIMEOUT_GRACE_S):
                # run_id: CLI 런타임의 작업 디렉터리·transcript 가 runs/<run_id>/ 에 붙는다(§A6)
                outcome = await self._runtime.run(
                    prompt, agent_tools(), session.call, limits=self._limits, run_id=run_id
                )
        except TimeoutError:
            outcome = AgentOutcome(ended=AgentEnd.TIME_LIMIT, tool_calls=session.calls)
        finally:
            # 정지(취소)면 닫지 않는다 — 앱이 브라우저째 닫고, 그때까지 가드는 켜진 채다(닫힌 쪽)
            if not _cancelling():
                await toolbox.close()
        return session, outcome

    async def _profile(self) -> Profile | None:
        try:
            return await self._profiles.get(self._user_id)
        except ProfileNotFound:
            return None

    async def _kb(self) -> Sequence[Answer]:
        return () if self._answers is None else await self._answers.list_answers(self._user_id)

    async def _start_run(self, application_id: str, run_id: str) -> None:
        run = RunRecord(
            run_id=run_id,
            application_id=application_id,
            kind=RunKind.FILL,
            started_at=self._clock.now(),
        )
        async with self._uow() as uow:
            await uow.runs.start(run)
            await uow.commit()

    async def _finish_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        outcome: AgentOutcome | None = None,
    ) -> None:
        async with self._uow() as uow:
            await uow.runs.finish(
                run_id,
                status,
                at=self._clock.now(),
                result=result,
                error=error,
                input_tokens=outcome.input_tokens if outcome else 0,
                output_tokens=outcome.output_tokens if outcome else 0,
            )
            await uow.commit()


def _cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0
