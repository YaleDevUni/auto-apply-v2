"""PlatformAdapter contract test (ARCHITECTURE.md §11.2).

ports/platform.py 의 계약:
  - matches() 로 스스로 담당 도메인만 인정한다
  - fetch_job() 이 실패하면(마감/비공개/삭제 등 재시도로 안 풀리는 실패) PolicyViolation
  - StaticPlatformRegistry.for_url() 은 매칭되는 어댑터가 없으면 PolicyViolation

WantedPlatformAdapter 는 `httpx.MockTransport` 로 오프라인 검증한다(`test_job_source_contract.py`
와 같은 패턴) — 파싱 로직은 매번 검증하되 네트워크는 타지 않는다. verify_submission 은 별도
인증 클라이언트(`_wanted_auth.wanted_cookie_client`)를 쓰므로 `test_attachment_contract.py`와
같은 방식으로 tmp_path 에 가짜 storage_state 를 만들어 검증한다.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.platform.saramin import SaraminPlatformAdapter
from auto_apply.adapters.platform.wanted import WantedPlatformAdapter
from auto_apply.contracts.dto import VerifyInput
from auto_apply.domain.errors import AuthRequired, PolicyViolation

_DUMMY_AUTH_DIR = Path("/nonexistent")  # fetch_job/evaluate/matches 는 인증을 안 타서 안전하다


def _auth_dir(tmp_path: Path) -> Path:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    state = {
        "cookies": [{"name": "session", "value": "x", "domain": ".wanted.co.kr", "path": "/"}],
        "origins": [],
    }
    (auth_dir / "wanted.json").write_text(json.dumps(state))
    return auth_dir


def _wanted_verify_handler(applications: list[dict], *, user_id: int = 2763813):
    """verify_submission 이 먼저 /api/v1/me 로 user_id 를 구하고 그걸로 /api/v1/applications 를

    조회하는 2단계 흐름(실측, 2026-08-20)을 오프라인으로 검증한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": user_id})
        assert request.url.params["user_id"] == str(user_id)
        assert request.url.params["job_id"] == "380443"
        return httpx.Response(200, json={"applications": applications})

    return handler


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

    return WantedPlatformAdapter(_mock_client(handler), auth_dir=_DUMMY_AUTH_DIR)


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
        adapter = WantedPlatformAdapter(
            _mock_client(lambda r: httpx.Response(200, json={})), auth_dir=_DUMMY_AUTH_DIR
        )
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

    async def test_verify_submission_without_job_id_or_since_is_unverified(self):
        """job_id/since 가 없으면 뭘 대조할지 모른다 — 네트워크도 안 타고 안전하게 떨어진다

        (§5 부분 제출 방어: 거짓 확인보다 미확인이 낫다).
        """
        adapter = _wanted_adapter()  # auth_dir 도 더미라 인증 클라이언트를 만들면 즉시 실패한다
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="wanted")
        )
        assert result.verified is False

    async def test_verify_submission_matches_recent_application(self, tmp_path: Path):
        since = datetime(2026, 8, 20, tzinfo=UTC)
        handler = _wanted_verify_handler(
            [{"job_id": 380443, "status": "complete", "create_time": "2026-08-20T09:10:00"}]
        )

        adapter = WantedPlatformAdapter(
            _mock_client(lambda r: httpx.Response(200)),
            auth_dir=_auth_dir(tmp_path),
            auth_transport=httpx.MockTransport(handler),
        )
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="wanted", job_id="wanted:380443", since=since)
        )
        assert result.verified is True

    async def test_verify_submission_tolerates_clock_skew(self, tmp_path: Path):
        """wanted 서버 시각과 워크플로우 시각(UTC) 사이 몇 분 오차는 오탐 방지 기준에서 봐준다."""
        since = datetime(2026, 8, 20, 9, 0, 0, tzinfo=UTC)
        # since 보다 3분 이르지만 _CLOCK_SKEW(5분) 안쪽 — KST 벽시계 문자열로 인코딩한다.
        created_instant = since - timedelta(minutes=3)
        create_time_kst_naive = created_instant.astimezone(ZoneInfo("Asia/Seoul")).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        handler = _wanted_verify_handler(
            [{"job_id": 380443, "status": "complete", "create_time": create_time_kst_naive}]
        )

        adapter = WantedPlatformAdapter(
            _mock_client(lambda r: httpx.Response(200)),
            auth_dir=_auth_dir(tmp_path),
            auth_transport=httpx.MockTransport(handler),
        )
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="wanted", job_id="wanted:380443", since=since)
        )
        assert result.verified is True

    async def test_verify_submission_ignores_stale_application(self, tmp_path: Path):
        """같은 공고에 과거(이번 시도 전)에 지원한 이력이 있어도 그걸로 오탐하지 않는다."""
        since = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
        handler = _wanted_verify_handler(
            [{"job_id": 380443, "status": "reject", "create_time": "2026-08-16T21:51:19"}]
        )

        adapter = WantedPlatformAdapter(
            _mock_client(lambda r: httpx.Response(200)),
            auth_dir=_auth_dir(tmp_path),
            auth_transport=httpx.MockTransport(handler),
        )
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="wanted", job_id="wanted:380443", since=since)
        )
        assert result.verified is False

    async def test_verify_submission_raises_auth_required_without_state_file(self, tmp_path: Path):
        adapter = WantedPlatformAdapter(
            _mock_client(lambda r: httpx.Response(200)),
            auth_dir=tmp_path / "no-such-dir",
        )
        with pytest.raises(AuthRequired):
            await adapter.verify_submission(
                VerifyInput(
                    application_id="a1",
                    platform="wanted",
                    job_id="wanted:380443",
                    since=datetime(2026, 8, 20, tzinfo=UTC),
                )
            )


def _saramin_detail_html(
    *,
    company: str = "지팩토리인터랙티브",
    title: str = "풀스택(웹) 신입 개발자 채용",
    deadline: str = "2099-12-31",
) -> str:
    og_title = f"[{company}] {title}(D-28) - 사람인"
    og_description = (
        f"{company}, {title}, 경력:경력무관, 학력:대학졸업이상, 마감일:{deadline}, 홈페이지:x.kr"
    )
    return (
        "<html><head>"
        f'<meta property="og:title" content="{og_title}" >'
        f'<meta property="og:description" content="{og_description}" >'
        "</head><body></body></html>"
    )


_SARAMIN_NOT_FOUND_HTML = '<html><head><meta property="og:title" content="사람인" ></head></html>'


def _saramin_adapter(
    status_code: int = 200, html: str = _saramin_detail_html()
) -> SaraminPlatformAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=html)

    return SaraminPlatformAdapter(_mock_client(handler))


class TestSaraminPlatformAdapter:
    def test_matches_saramin_host_only(self):
        adapter = SaraminPlatformAdapter(_mock_client(lambda r: httpx.Response(200)))
        assert adapter.matches("https://www.saramin.co.kr/zf_user/member/apply?rec_idx=1") is True
        assert adapter.matches("https://www.wanted.co.kr/wd/1") is False

    async def test_fetch_job_parses_og_meta(self):
        adapter = _saramin_adapter()
        job = await adapter.fetch_job(
            "https://www.saramin.co.kr/zf_user/member/apply?rec_idx=54782535"
        )
        assert job.job_id == "saramin:54782535"
        assert job.platform == "saramin"
        assert job.title == "풀스택(웹) 신입 개발자 채용"
        assert job.company == "지팩토리인터랙티브"
        assert "마감일:2099-12-31" in job.description

    async def test_fetch_job_rejects_url_without_rec_idx(self):
        adapter = _saramin_adapter()
        with pytest.raises(PolicyViolation):
            await adapter.fetch_job("https://www.saramin.co.kr/zf_user/search/recruit")

    async def test_fetch_job_rejects_not_found_posting(self):
        """마감/비공개/삭제된 공고는 og:title 이 브랜드명("사람인")뿐이다(실측)."""
        adapter = _saramin_adapter(html=_SARAMIN_NOT_FOUND_HTML)
        with pytest.raises(PolicyViolation):
            await adapter.fetch_job(
                "https://www.saramin.co.kr/zf_user/member/apply?rec_idx=99999999"
            )

    async def test_fetch_job_rejects_http_404(self):
        adapter = _saramin_adapter(status_code=404, html=_SARAMIN_NOT_FOUND_HTML)
        with pytest.raises(PolicyViolation):
            await adapter.fetch_job(
                "https://www.saramin.co.kr/zf_user/member/apply?rec_idx=99999999"
            )

    async def test_evaluate_eligible_when_deadline_in_future(self):
        adapter = _saramin_adapter()
        job = await adapter.fetch_job(
            "https://www.saramin.co.kr/zf_user/member/apply?rec_idx=54782535"
        )
        verdict = await adapter.evaluate(job)
        assert verdict.eligible is True

    async def test_evaluate_rejects_when_deadline_passed(self):
        adapter = _saramin_adapter(html=_saramin_detail_html(deadline="2000-01-01"))
        job = await adapter.fetch_job(
            "https://www.saramin.co.kr/zf_user/member/apply?rec_idx=54782535"
        )
        verdict = await adapter.evaluate(job)
        assert verdict.eligible is False
        assert "마감" in verdict.reason

    async def test_verify_submission_is_always_unverified(self):
        """미구현 — 로그인 세션 확보 방식이 아직 실측 검증 안 됨 (거짓 확인보다 안전)."""
        adapter = _saramin_adapter()
        result = await adapter.verify_submission(
            VerifyInput(application_id="a1", platform="saramin")
        )
        assert result.verified is False


class TestStaticPlatformRegistry:
    def test_for_url_dispatches_to_matching_adapter(self):
        registry = StaticPlatformRegistry(
            [
                FixturePlatformAdapter(),
                WantedPlatformAdapter(
                    _mock_client(lambda r: httpx.Response(200)), auth_dir=_DUMMY_AUTH_DIR
                ),
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
