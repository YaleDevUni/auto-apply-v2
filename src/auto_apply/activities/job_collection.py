"""공고 수집 activity 구현. port 만 주입받는다 (§11.3).

`collect_platform_jobs` 하나가 플랫폼 하나의 파이프라인 전체를 담당한다 —
목록 수집 → 스크리닝 → 통과분만 상세 조회 → 재스크리닝 → 지원가능성 판정 → 저장.
구 프로젝트 workflows/collect.py 의 4단계 순서를 그대로 따른다: 요청 수가 제일
비싼 상세 조회를, 이미 하드컷으로 거를 게 뻔한 공고에는 쓰지 않는다.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import structlog
from temporalio import activity

from auto_apply.contracts.job import (
    JobPosting,
    JobRecord,
    PlatformCollectionResult,
    ScreeningVerdict,
)
from auto_apply.domain.errors import PolicyViolation
from auto_apply.domain.job_applicability import evaluate_applicability
from auto_apply.domain.job_screening import screen
from auto_apply.ports.clock import Clock
from auto_apply.ports.job_source import JobSource
from auto_apply.ports.matching_config import MatchingConfigSource
from auto_apply.ports.recipe_source import RecipeSource
from auto_apply.ports.repository import UnitOfWork

log = structlog.get_logger(__name__)


class JobCollectionActivities:
    def __init__(
        self,
        job_sources: Sequence[JobSource],
        matching_config: MatchingConfigSource,
        recipes: RecipeSource,
        clock: Clock,
        uow: Callable[[], UnitOfWork],
        *,
        auth_dir: Path,
        detail_limit: int = 200,
    ) -> None:
        self._sources = {s.platform: s for s in job_sources}
        self._matching_config = matching_config
        self._recipes = recipes
        self._clock = clock
        self._uow = uow
        self._auth_dir = auth_dir
        self._detail_limit = detail_limit

    @activity.defn(name="collect_platform_jobs")
    async def collect_platform_jobs(self, platform: str) -> PlatformCollectionResult:
        source = self._source(platform)
        cfg = await self._matching_config.load()
        recipe_exists = await self._recipe_exists(platform)
        session_ok = self._session_ok(platform)
        wf_id = activity.info().workflow_id

        # ── 1차: 목록 데이터만으로 판정 ──
        staged: dict[str, tuple[JobPosting, ScreeningVerdict]] = {}
        found = 0
        async for job in source.list_jobs():
            found += 1
            if found % 25 == 0:
                activity.heartbeat(found)
            staged[job.platform_job_id] = (job, screen(job, cfg))

        passed = sorted(
            (item for item in staged.values() if item[1].verdict == "pass"),
            key=lambda item: item[1].fit_score,
            reverse=True,
        )

        # ── 2차: 통과분만 상세 조회, 본문이 붙은 상태로 재판정 ──
        enrich_errors = 0
        for job, first in passed[: self._detail_limit]:
            activity.heartbeat(f"enrich:{job.platform_job_id}")
            try:
                enriched = await source.enrich(job)
            except Exception as exc:
                enrich_errors += 1
                log.warning(
                    "job_collection.enrich_failed",
                    workflow_id=wf_id,
                    platform=platform,
                    job_id=job.platform_job_id,
                    error=str(exc),
                )
                continue
            final = screen(enriched, cfg) if enriched.description else first
            staged[job.platform_job_id] = (enriched, final)

        # ── 저장 + 지원가능성 판정 ──
        passed_count = 0
        excluded_count = 0
        actionable_count = 0
        collected_at = self._clock.now()

        async with self._uow() as uow:
            for job, verdict in staged.values():
                applicability = None
                if verdict.verdict == "pass":
                    passed_count += 1
                    applicability = evaluate_applicability(
                        job,
                        verdict,
                        cfg.applicability,
                        recipe_exists=recipe_exists,
                        session_ok=session_ok,
                        # M2: 이력서 조립 결과(§ job_applicability.py 5-b)와 아직
                        # 안 이어져 있다. 조립 파이프라인이 붙으면 실제 값을 넘긴다.
                        required_gaps=0,
                    )
                    if applicability.actionable:
                        actionable_count += 1
                else:
                    excluded_count += 1
                await uow.jobs.upsert(
                    JobRecord(
                        job=job,
                        screening=verdict,
                        applicability=applicability,
                        collected_at=collected_at,
                    )
                )
            await uow.commit()

        log.info(
            "job_collection.platform_done",
            workflow_id=wf_id,
            platform=platform,
            found=found,
            passed=passed_count,
            actionable=actionable_count,
            enrich_errors=enrich_errors,
        )
        return PlatformCollectionResult(
            platform=platform,
            found=found,
            passed=passed_count,
            excluded=excluded_count,
            actionable=actionable_count,
            enrich_errors=enrich_errors,
        )

    def _source(self, platform: str) -> JobSource:
        try:
            return self._sources[platform]
        except KeyError as e:
            raise PolicyViolation(f"등록되지 않은 JobSource: {platform}") from e

    async def _recipe_exists(self, platform: str) -> bool:
        try:
            await self._recipes.active(platform)
            return True
        except PolicyViolation:
            return False

    def _session_ok(self, platform: str) -> bool:
        """storage_state 파일 존재만 본다 — 실제 유효성은 실행 시점에만 안다

        (playwright.py 의 AuthRequired 가 그때 잡는다). 파일이 없으면 로그인을
        아예 한 적이 없다는 확실한 신호이므로 False, 있으면 True 로 낙관한다.
        """
        return (self._auth_dir / f"{platform}.json").is_file()

    def all(self) -> list[Callable[..., Any]]:
        return [self.collect_platform_jobs]
