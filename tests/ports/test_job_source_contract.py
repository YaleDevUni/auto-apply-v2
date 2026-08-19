"""JobSource contract test — 네 구현(fixture + 실제 3플랫폼)이 같은 계약을 지킨다.

실제 플랫폼 어댑터도 `httpx.MockTransport`로 오프라인 검증한다 — 파싱 로직은
매번 검증하되, 네트워크는 타지 않는다 (make test 는 인프라 없이 항상 돈다).
"""

import json

import httpx
import pytest

from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.job_source.jasoseol import JasoseolJobSource
from auto_apply.adapters.job_source.saramin import SaraminJobSource
from auto_apply.adapters.job_source.wanted import DEFAULT_JOB_GROUPS, WantedJobSource
from auto_apply.adapters.storage.memory import InMemoryBlobStore

WANTED_LIST = {
    "data": [
        {
            "id": 111,
            "position": "백엔드 엔지니어",
            "company": {"name": "원티드 주식회사"},
            "address": {"location": "서울", "district": "강남구"},
            "due_time": None,
            "title_img": {"origin": "https://x/thumb.png"},
            "reward_total": 100000,
        }
    ]
}
WANTED_DETAIL = {
    "job": {
        "detail": {
            "intro": "회사 소개",
            "main_tasks": "백엔드 개발",
            "requirements": "Python 3년",
        },
        "skill_tags": [{"title": "Python"}],
        "address": {"full_location": "서울 강남구"},
        "due_time": "2026-12-31",
    }
}

SARAMIN_HTML = """
<div class="item_recruit" value="222">
  <div class="job_tit">
    <a href="/zf_user/jobs/relay/view?rec_idx=222" title="사람인 백엔드 개발자">
      사람인 백엔드 개발자
    </a>
  </div>
  <div class="area_corp"><strong class="corp_name"><a href="#">사람인테스트</a></strong></div>
  <div class="job_condition">
    <a>서울</a><span>신입</span><span>대졸</span><span>정규직</span><span>3000만원</span>
  </div>
  <div class="job_sector">개발 등록일 26/08/01</div>
  <div class="job_date"><span class="date">~ 12/31(목)</span></div>
</div>
"""

JASOSEOL_LIST = [
    {
        "id": 333,
        "name": "자소설컴퍼니",
        "title": "자소설컴퍼니 채용",
        "employments": [{"id": 1, "field": "백엔드", "has_resume": True}],
    }
]
JASOSEOL_DETAIL = {
    "content": "<p>본문입니다</p><img src='https://x/poster.png'>",
}


def _mock_client(handler) -> ThrottledClient:
    transport = httpx.MockTransport(handler)
    return ThrottledClient(httpx.AsyncClient(transport=transport), delay=0, retries=1)


def _wanted_source() -> WantedJobSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if "details" in request.url.path:
            return httpx.Response(200, json=WANTED_DETAIL)
        return httpx.Response(200, json=WANTED_LIST)

    return WantedJobSource(_mock_client(handler), job_groups=[DEFAULT_JOB_GROUPS[0]], pages=1)


def _saramin_source() -> SaraminJobSource:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:
            return httpx.Response(200, text="<html></html>")  # 다음 페이지는 빈 목록
        return httpx.Response(200, text=SARAMIN_HTML)

    return SaraminJobSource(_mock_client(handler), keywords=["테스트"], companies=[], pages=1)


def _jasoseol_source() -> JasoseolJobSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/333"):
            return httpx.Response(200, json=JASOSEOL_DETAIL)
        return httpx.Response(200, text=json.dumps(JASOSEOL_LIST))

    return JasoseolJobSource(_mock_client(handler), InMemoryBlobStore())


@pytest.fixture(params=["fixture", "wanted", "saramin", "jasoseol"])
def source(request: pytest.FixtureRequest):
    return {
        "fixture": lambda: FixtureJobSource(),
        "wanted": _wanted_source,
        "saramin": _saramin_source,
        "jasoseol": _jasoseol_source,
    }[request.param]()


async def test_platform_is_a_non_empty_identifier(source):
    assert source.platform


async def test_list_jobs_yields_well_formed_postings_for_its_own_platform(source):
    jobs = [j async for j in source.list_jobs()]
    assert jobs
    for job in jobs:
        assert job.platform == source.platform
        assert job.platform_job_id
        assert job.url
        assert job.title
        assert job.company


async def test_list_jobs_has_no_duplicate_ids_within_one_pass(source):
    jobs = [j async for j in source.list_jobs()]
    ids = [j.platform_job_id for j in jobs]
    assert len(ids) == len(set(ids))


async def test_enrich_preserves_identity(source):
    jobs = [j async for j in source.list_jobs()]
    enriched = await source.enrich(jobs[0])
    assert enriched.platform == jobs[0].platform
    assert enriched.platform_job_id == jobs[0].platform_job_id


async def test_wanted_enrich_fills_description_and_deadline():
    source = _wanted_source()
    job = await anext(source.list_jobs())
    enriched = await source.enrich(job)
    assert "백엔드 개발" in enriched.description
    assert enriched.deadline == "2026-12-31"


async def test_saramin_enrich_is_a_documented_noop():
    """카드 자체에 필터에 필요한 정보가 다 있어 상세 조회를 하지 않는다."""
    source = _saramin_source()
    job = await anext(source.list_jobs())
    assert await source.enrich(job) == job


async def test_jasoseol_enrich_stores_image_via_blobstore():
    store = InMemoryBlobStore()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/333"):
            return httpx.Response(200, json=JASOSEOL_DETAIL)
        return httpx.Response(200, text=json.dumps(JASOSEOL_LIST))

    source = JasoseolJobSource(_mock_client(handler), store)
    job = await anext(source.list_jobs())
    enriched = await source.enrich(job)
    assert enriched.image_path is not None
    assert await store.exists(enriched.image_path)


def test_default_job_groups_are_unique():
    assert len({g.id for g in DEFAULT_JOB_GROUPS}) == len(DEFAULT_JOB_GROUPS)
