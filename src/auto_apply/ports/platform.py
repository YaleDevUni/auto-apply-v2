from typing import Protocol

from auto_apply.contracts.dto import Eligibility, JobRef


class PlatformAdapter(Protocol):
    """플랫폼별 공고 수집/판정. registry 로 등록해 확장한다 (§11.2)."""

    @property
    def platform(self) -> str: ...

    def matches(self, url: str) -> bool: ...

    async def fetch_job(self, url: str) -> JobRef: ...

    async def evaluate(self, job: JobRef) -> Eligibility: ...
