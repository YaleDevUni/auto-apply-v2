"""재진입 fill run — 답이 늦게 온 질문을 받아 fill 을 이어서 다시 돈다 (§A5 ask_user, D8·D10).

ask_user 가 시간 안에 답을 못 받으면 run 은 NEEDS_INPUT 으로 끝나고, 질문(HumanTask)과 FillLog
부분 기록이 `runs/<run_id>/` 에 남는다. 사람이 나중에 답하면(`FillReentry.answer`, UI 는 M4) 답을
갈무리하고 같은 지원 건에 fill job 을 넣는다 — 새 run 은 같은 URL 을 다시 열고 직전 FillLog 와 받은
답을 프롬프트로 받아 값을 다시 넣는다(새 FillLog 에 새로 쌓인다).

가린 답(sensitive·주민번호 꼴)은 DB·job payload 에 싣지 않고 `HeldAnswers`(프로세스 메모리)에만
둔다(절대 규칙 5). 앱이 다시 시작되면 사라지고, 에이전트는 다시 묻는다.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol

import structlog
from pydantic import ValidationError

from auto_apply.ai.fill_prompt import FillResume
from auto_apply.contracts.fill_log import FillLog, FillSource, FillSourceKind
from auto_apply.contracts.human_gate import HumanTaskKind
from auto_apply.contracts.jobs import JobKind, JobRecord, JobStatus
from auto_apply.contracts.knowledge import Answer
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.unique_identifiers import contains_resident_registration_number
from auto_apply.ports.repository import UnitOfWork
from auto_apply.services.application import ApplicationService
from auto_apply.services.browser_toolbox import AnswerSink
from auto_apply.services.profile import DEFAULT_USER_ID
from auto_apply.services.run_artifacts import RunArtifacts

log = structlog.get_logger(__name__)

Enqueue = Callable[..., Awaitable[JobRecord]]  # JobRunner.enqueue


class AnswerBook(Protocol):
    """답변 KB 읽기 (ProfileService 가 만족한다) — fill 프롬프트에 싣는다(D10)."""

    async def list_answers(self, user_id: str) -> list[Answer]: ...


class HeldAnswers:
    """가린 답 — 지원 건 → {핸들: 값}. 메모리에만 있다(저장·로그 없음)."""

    def __init__(self) -> None:
        self._by_app: dict[str, dict[str, str]] = {}

    def get(self, application_id: str) -> dict[str, str]:
        return dict(self._by_app.get(application_id, {}))

    def keep(self, application_id: str, answers: Mapping[str, str]) -> None:
        if answers:
            self._by_app.setdefault(application_id, {}).update(answers)

    def drop(self, application_id: str) -> None:
        self._by_app.pop(application_id, None)


class FillReentry:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        applications: ApplicationService,
        artifacts: RunArtifacts,
        answers: AnswerSink,
        held: HeldAnswers,
        enqueue: Enqueue,
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self._uow, self._apps, self._artifacts = uow, applications, artifacts
        self._answers, self._held, self._enqueue = answers, held, enqueue
        self._user_id = user_id

    async def answer(self, run_id: str, answer: str) -> JobRecord:
        """`run_id` 가 답을 못 받고 끝낸 질문에 답한다 → 재진입 fill job."""
        task = await self._artifacts.load_human_task(run_id)
        if task is None or task.kind is not HumanTaskKind.QUESTION or task.application_id is None:
            raise NotFound("답을 기다리는 질문이 없는 run 이다")
        app_id = task.application_id
        if await self._apps.current_state(app_id) is not ApplicationState.NEEDS_INPUT:
            raise InvalidInput("답을 기다리는 지원 건이 아니다")
        if not answer.strip():
            raise InvalidInput("답이 비었다")
        if await self._fill_pending(app_id):
            raise InvalidInput("이 지원 건은 이미 이어서 돌 fill 이 있다")
        if task.sensitive or contains_resident_registration_number(answer):
            self._held.keep(app_id, {task.id: answer})
            source = FillSource(kind=FillSourceKind.USER, key=task.id)
        else:
            row = await self._answers.remember_answer(
                self._user_id, task.question, answer, application_id=app_id
            )
            source = FillSource(kind=FillSourceKind.ANSWER_KB, key=row.id)
        payload = {"resume_from": run_id, "answer": source.model_dump(mode="json")}
        job = await self._enqueue(JobKind.FILL, application_id=app_id, payload=payload)
        log.info("fill.reentry_queued", application_id=app_id, run_id=run_id, job_id=job.job_id)
        return job

    async def _fill_pending(self, application_id: str) -> bool:
        async with self._uow() as uow:
            jobs = [
                *await uow.jobs.list_by_status(JobStatus.QUEUED),
                *await uow.jobs.list_by_status(JobStatus.RUNNING),
            ]
        return any(j.application_id == application_id and j.kind is JobKind.FILL for j in jobs)


def resume_request(payload: Mapping[str, object]) -> tuple[str, FillSource] | None:
    """fill job payload → (직전 run_id, 받은 답의 source). 재진입이 아니면 None."""
    prev = payload.get("resume_from")
    if prev is None:
        return None
    if not isinstance(prev, str) or not prev:
        raise InvalidInput("resume_from 이 run id 가 아니다")
    try:
        return prev, FillSource.model_validate(payload.get("answer"))
    except ValidationError as e:
        raise InvalidInput("재진입 답의 source 가 맞지 않다") from e


async def load_resume(
    artifacts: RunArtifacts,
    request: tuple[str, FillSource],
    answers: Sequence[Answer],
    held: Mapping[str, str],
) -> FillResume:
    """재진입 run 의 프롬프트 재료 — 직전 run 의 FillLog 부분 기록·질문과 받은 답."""
    prev, source = request
    fill_log = await artifacts.load_fill_log(prev) or FillLog()
    task = await artifacts.load_human_task(prev)
    question = task.question if task is not None else ""
    value = None
    if source.kind is FillSourceKind.ANSWER_KB:
        value = next((a.answer for a in answers if a.id == source.key), None)
    return FillResume(
        fill_log=fill_log,
        question=question,
        source=source,
        value=value,
        held=source.kind is FillSourceKind.USER and source.key in held,
    )
