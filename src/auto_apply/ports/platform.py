from typing import Protocol

from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult


class PlatformAdapter(Protocol):
    """플랫폼별 공고 수집/판정/제출확인. registry 로 등록해 확장한다 (§11.2)."""

    @property
    def platform(self) -> str: ...

    def matches(self, url: str) -> bool: ...

    async def fetch_job(self, url: str) -> JobRef: ...

    async def evaluate(self, job: JobRef) -> Eligibility: ...

    async def verify_submission(self, inp: VerifyInput) -> VerifyResult: ...


class PlatformRegistry(Protocol):
    def for_url(self, url: str) -> PlatformAdapter:
        """매칭되는 어댑터가 없으면 domain.errors.PolicyViolation (계약).

        모르는 도메인에 자동화를 돌리지 않는다 — allowlist 방식 (§3).
        """
        ...

    def for_platform(self, platform: str) -> PlatformAdapter: ...
