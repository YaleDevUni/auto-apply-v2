from collections.abc import AsyncIterator, Sequence

from auto_apply.contracts.job import JobPosting


class FixtureJobSource:
    """네트워크 없이 파이프라인을 돌리기 위한 어댑터. 실제 WantedJobSource 등은

    같은 port 로 나란히 존재한다 (adapters/platform/fixture.py 와 같은 역할).
    """

    def __init__(
        self, jobs: Sequence[JobPosting] | None = None, *, platform: str = "fixture"
    ) -> None:
        self._platform = platform
        self._jobs = list(jobs) if jobs is not None else [_default_job(platform)]

    @property
    def platform(self) -> str:
        return self._platform

    async def list_jobs(self) -> AsyncIterator[JobPosting]:
        for job in self._jobs:
            yield job

    async def enrich(self, job: JobPosting) -> JobPosting:
        if job.description:
            return job
        return job.model_copy(update={"description": "[고정 fixture 본문] 백엔드 엔지니어 채용"})


def _default_job(platform: str) -> JobPosting:
    return JobPosting(
        platform=platform,
        platform_job_id="1",
        url=f"https://{platform}.local/jobs/1",
        company="Fixture Inc.",
        title="백엔드 엔지니어 신입",
        category="개발",
        description="",
    )
