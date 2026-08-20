"""PlatformAdapter contract test (ARCHITECTURE.md §11.2).

ports/platform.py 의 계약:
  - matches() 로 스스로 담당 도메인만 인정한다
  - fetch_job() 이 실패하면(마감/비공개/삭제 등 재시도로 안 풀리는 실패) PolicyViolation
  - StaticPlatformRegistry.for_url() 은 매칭되는 어댑터가 없으면 PolicyViolation

WantedPlatformAdapter 는 `httpx.MockTransport` 로 오프라인 검증한다(`test_job_source_contract.py`
와 같은 패턴) — 파싱 로직은 매번 검증하되 네트워크는 타지 않는다.
"""

import httpx
import pytest

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.platform.wanted import WantedPlatformAdapter
from auto_apply.contracts.dto import VerifyInput
from auto_apply.domain.errors import PolicyViolation

WANTED_DETAIL = {
    "job": {
        "position": "백엔드 엔지니어",
        "company": {"name": "원티드 주식회사"},
        "detail": {"intro": "회사 소개", "main_tasks": "백엔드 개발", "status": "open"},
        "skill_tags": [{"title": "Python"}],
    }
}
WANTED_DETAIL_CLOSED = {
    "job": {
        "position": "백엔드 엔지니어",
        "company": {"name": "원티드 주식회사"},
        "detail": {"intro": "회사 소개", "main_tasks": "백엔드 개발", "status": "close"},
        "skill_tags": [{"title": "Python"}],
    }
}


def _mock_client(handler) -> ThrottledClient:
    return ThrottledClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), delay=0, retries=1
    )


def _wanted_adapter(status_code: int = 200, body: object = WANTED_DETAIL) -> WantedPlatformAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return WantedPlatformAdapter(_mock_client(handler))


class TestFixturePlatformAdapter:
    async def test_matches_own_host_only(self):
        adapter = FixturePlatformAdapter()
        assert adapter.matches("https://fixture.local/job/1") is True
        assert adapter.matches("https://www.wanted.co.kr/wd/1") is False

    async def test_fetch_and_evaluate_round_trip(self):
        adapter = FixturePlatformAdapter()
        job = await adapter.fetch_job("https://fixture.local/job/42")
        assert job.platform == "fixture"
        verdict = await adapter.evaluate(job)
        assert verdict.eligible is True

    async def test_verify_submission_configurable(self):
        adapter = FixturePlatformAdapter(verified=False)
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="fixture")
        )
        assert result.verified is False


class TestWantedPlatformAdapter:
    def test_matches_wanted_host_only(self):
        adapter = WantedPlatformAdapter(_mock_client(lambda r: httpx.Response(200, json={})))
        assert adapter.matches("https://www.wanted.co.kr/wd/373300") is True
        assert adapter.matches("https://fixture.local/job/1") is False

    async def test_fetch_job_parses_detail(self):
        adapter = _wanted_adapter()
        job = await adapter.fetch_job("https://www.wanted.co.kr/wd/373300")
        assert job.job_id == "wanted:373300"
        assert job.platform == "wanted"
        assert job.title == "백엔드 엔지니어"
        assert job.company == "원티드 주식회사"
        assert "회사 소개" in job.description
        assert "Python" in job.description

    async def test_fetch_job_rejects_malformed_url(self):
        adapter = _wanted_adapter()
        with pytest.raises(PolicyViolation):
            await adapter.fetch_job("https://www.wanted.co.kr/company/1")

    async def test_fetch_job_rejects_missing_posting(self):
        adapter = _wanted_adapter(body={})  # job 필드 없음 = 마감/비공개/삭제
        with pytest.raises(PolicyViolation):
            await adapter.fetch_job("https://www.wanted.co.kr/wd/999999")

    async def test_evaluate_eligible_when_open(self):
        adapter = _wanted_adapter()
        job = await adapter.fetch_job("https://www.wanted.co.kr/wd/373300")
        verdict = await adapter.evaluate(job)
        assert verdict.eligible is True

    async def test_evaluate_rejects_when_closed(self):
        """실측(2026-08-20): 지원마감 공고는 fetch_job 은 통과하지만 지원 버튼이 disabled 라

        Recipe 실행이 "첨부파일 선택" 대기에서 timeout 난다 — evaluate 에서 미리 걸러야
        이력서 생성/텔레그램 승인까지 낭비하지 않는다.
        """
        adapter = _wanted_adapter(body=WANTED_DETAIL_CLOSED)
        job = await adapter.fetch_job("https://www.wanted.co.kr/wd/373300")
        verdict = await adapter.evaluate(job)
        assert verdict.eligible is False
        assert "마감" in verdict.reason

    async def test_verify_submission_never_falsely_confirms(self):
        """검증 API 가 아직 없다 — 항상 unverified 로 안전하게 떨어진다(§5 부분 제출 방어)."""
        adapter = _wanted_adapter()
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="wanted")
        )
        assert result.verified is False


class TestStaticPlatformRegistry:
    def test_for_url_dispatches_to_matching_adapter(self):
        registry = StaticPlatformRegistry(
            [
                FixturePlatformAdapter(),
                WantedPlatformAdapter(_mock_client(lambda r: httpx.Response(200))),
            ]
        )
        assert registry.for_url("https://www.wanted.co.kr/wd/1").platform == "wanted"
        assert registry.for_url("https://fixture.local/job/1").platform == "fixture"

    def test_for_url_unregistered_domain_raises(self):
        registry = StaticPlatformRegistry([FixturePlatformAdapter()])
        with pytest.raises(PolicyViolation):
            registry.for_url("https://www.saramin.co.kr/job/1")

    def test_for_platform_unregistered_raises(self):
        registry = StaticPlatformRegistry([FixturePlatformAdapter()])
        with pytest.raises(PolicyViolation):
            registry.for_platform("wanted")
