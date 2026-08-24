"""Application 관련 activity 구현. port 만 주입받는다 (§11.3)."""

from collections.abc import Callable
from typing import Any

import structlog
from temporalio import activity

from auto_apply.contracts.dto import (
    ApplicationAttempt,
    CachedResume,
    DecisionRequest,
    DecisionTicket,
    Eligibility,
    JobRef,
    NotifyEvent,
    PersistState,
    VerifyInput,
    VerifyResult,
)
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.platform import PlatformRegistry
from auto_apply.ports.recipe_source import RecipeSource
from auto_apply.ports.repository import UnitOfWork

log = structlog.get_logger(__name__)


class ApplicationActivities:
    def __init__(
        self,
        registry: PlatformRegistry,
        notifier: Notifier,
        recipes: RecipeSource,
        uow: Callable[[], UnitOfWork],
    ) -> None:
        self._registry = registry
        self._notifier = notifier
        self._recipes = recipes
        self._uow = uow

    @activity.defn(name="collect_job")
    async def collect_job(self, job_url: str) -> JobRef:
        return await self._registry.for_url(job_url).fetch_job(job_url)

    @activity.defn(name="evaluate_eligibility")
    async def evaluate_eligibility(self, job: JobRef) -> Eligibility:
        return await self._registry.for_platform(job.platform).evaluate(job)

    @activity.defn(name="load_active_recipe")
    async def load_active_recipe(self, platform: str) -> AutomationRecipe:
        return await self._recipes.active(platform)

    @activity.defn(name="request_approval")
    async def request_approval(self, req: DecisionRequest) -> DecisionTicket:
        return await self._notifier.request_decision(req)

    @activity.defn(name="notify")
    async def notify(self, event: NotifyEvent) -> None:
        await self._notifier.notify(event)

    @activity.defn(name="verify_submission")
    async def verify_submission(self, inp: VerifyInput) -> VerifyResult:
        return await self._registry.for_platform(inp.platform).verify_submission(inp)

    @activity.defn(name="persist_state")
    async def persist_state(self, state: PersistState) -> None:
        """DB projection 을 쓰는 유일한 통로 (§4.1). 멱등해야 한다."""
        async with self._uow() as uow:
            await uow.applications.upsert_state(state)
            await uow.commit()
        log.info(
            "state.persisted",
            application_id=state.application_id,
            state=state.state,
            workflow_id=activity.info().workflow_id,
        )

    @activity.defn(name="record_attempt")
    async def record_attempt(self, attempt: ApplicationAttempt) -> None:
        """`application_attempts` 감사 로그를 쓰는 유일한 통로 (§4, §5). 멱등해야 한다."""
        async with self._uow() as uow:
            await uow.attempts.record(attempt)
            await uow.commit()
        log.info(
            "attempt.recorded",
            application_id=attempt.application_id,
            attempt=attempt.attempt,
            outcome=attempt.outcome,
            mode=attempt.mode,
            workflow_id=activity.info().workflow_id,
        )

    @activity.defn(name="get_cached_resume")
    async def get_cached_resume(self, application_id: str) -> CachedResume | None:
        async with self._uow() as uow:
            return await uow.resumes.get(application_id)

    @activity.defn(name="save_cached_resume")
    async def save_cached_resume(self, resume: CachedResume) -> None:
        """이력서 재사용 캐시를 쓰는 유일한 통로 (§2.3). 멱등해야 한다."""
        async with self._uow() as uow:
            await uow.resumes.save(resume)
            await uow.commit()
        log.info(
            "resume.cached",
            application_id=resume.application_id,
            workflow_id=activity.info().workflow_id,
        )

    def all(self) -> list[Callable[..., Any]]:
        return [
            self.collect_job,
            self.evaluate_eligibility,
            self.load_active_recipe,
            self.request_approval,
            self.notify,
            self.verify_submission,
            self.persist_state,
            self.record_attempt,
            self.get_cached_resume,
            self.save_cached_resume,
        ]
