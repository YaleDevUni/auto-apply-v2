from urllib.parse import urlparse

from auto_apply.contracts.dto import Eligibility, JobRef, VerifyInput, VerifyResult


class FixturePlatformAdapter:
    """네트워크 없이 파이프라인을 돌리기 위한 어댑터.

    실제 WantedAdapter 는 M2 에서 같은 port 로 추가한다.
    """

    def __init__(
        self,
        platform: str = "fixture",
        hosts: tuple[str, ...] = ("fixture.local",),
        *,
        eligible: bool = True,
        reject_reason: str = "",
        verified: bool = True,
    ) -> None:
        self._platform = platform
        self._hosts = hosts
        self._eligible = eligible
        self._reject_reason = reject_reason
        self._verified = verified

    @property
    def platform(self) -> str:
        return self._platform

    def matches(self, url: str) -> bool:
        return (urlparse(url).hostname or "") in self._hosts

    async def fetch_job(self, url: str) -> JobRef:
        path = urlparse(url).path.strip("/") or "unknown"
        return JobRef(
            job_id=f"{self._platform}:{path}",
            platform=self._platform,
            url=url,
            title="백엔드 엔지니어",
            company="Fixture Inc.",
        )

    async def evaluate(self, job: JobRef) -> Eligibility:
        return Eligibility(eligible=self._eligible, reason=self._reject_reason)

    async def verify_submission(self, inp: VerifyInput) -> VerifyResult:
        return VerifyResult(verified=self._verified, detail="fixture")
