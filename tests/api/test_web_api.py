"""§12 웹 콘솔 전용 엔드포인트(`api/routers/web.py`) — `test_applications_api.py`와 같은

real time-skipping Temporal 서버 + 워커 패턴을 재사용한다.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport
from temporalio.testing import WorkflowEnvironment

from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.api.main import app
from auto_apply.contracts.dto import ApplicationResult
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.job_identity import canonical_key
from auto_apply.temporal_config import DATA_CONVERTER
from tests.conftest import JOB_URL, Harness
from tests.workflows.test_application import _wait_state, _Workers

pytestmark = pytest.mark.temporal

# FixturePlatformAdapter.fetch_job 이 항상 내주는 회사/직무(platform 이름과 무관) — apply_by_url
# 은 wanted 로 등록된 어댑터만 허용하므로(§ apply_intake._APPLY_BY_URL_PLATFORMS), platform="wanted"
# 로 등록하되 JOB_URL 의 호스트(fixture.local)에 매칭시킨다.
_JOB = JobPosting(
    platform="fixture",
    platform_job_id="fixture:jobs/1",
    url=JOB_URL,
    company="Fixture Inc.",
    title="백엔드 엔지니어",
)
_APPLICATION_ID = canonical_key(_JOB.company, _JOB.title)


def _wanted_registry() -> StaticPlatformRegistry:
    adapter = FixturePlatformAdapter(platform="wanted", hosts=("fixture.local",))
    return StaticPlatformRegistry([adapter])


async def _poll(check, *, attempts: int = 200, delay: float = 0.05):
    """조건을 만족하는 마지막 호출 결과를 돌려준다 — activity 실행 지연(nonce 발급 등)이

    걸린 query/REST 응답을 기다릴 때 쓴다. 시간 초과면 마지막 결과를 그대로 돌려줘서 실패
    지점의 assert 메시지가 실제 응답을 보여주게 한다.
    """
    result = None
    for _ in range(attempts):
        result = await check()
        if result:
            return result
        await asyncio.sleep(delay)
    return result


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


@pytest.fixture
async def client(env: WorkflowEnvironment):
    h = Harness(
        job_rows={
            (_JOB.platform, _JOB.platform_job_id): JobRecord(
                job=_JOB,
                screening=ScreeningVerdict(verdict="pass", fit_score=50),
                applicability=ApplicabilityVerdict(
                    actionable=True, channel="platform_form", apply_url=_JOB.url
                ),
                collected_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
            )
        },
        registry=_wanted_registry(),
    )
    app.state.container = h.container()
    app.state.temporal_client = env.client
    async with (
        _Workers(env.client, h),
        httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac, env, h


async def test_list_applications_joins_job_cache(client):
    ac, _env, _h = client
    resp = await ac.post("/applications/apply-by-url", json={"url": JOB_URL})
    assert resp.status_code == 202
    body = resp.json()
    assert body["outcome"] == "started"
    application_id = body["application_id"]
    assert application_id == _APPLICATION_ID

    async def _find_row():
        listed = await ac.get("/applications")
        assert listed.status_code == 200
        return next(
            (i for i in listed.json()["items"] if i["application_id"] == application_id), None
        )

    row = await _poll(_find_row)
    assert row is not None, "지원 건이 목록에 나타나지 않았다"
    assert row["company"] == _JOB.company
    assert row["title"] == _JOB.title
    assert row["job_url"] == _JOB.url


async def test_pending_decision_has_resume_url_while_awaiting_approval(client):
    ac, env, _h = client
    resp = await ac.post("/applications/apply-by-url", json={"url": JOB_URL})
    application_id = resp.json()["application_id"]
    handle = env.client.get_workflow_handle(
        f"application-{application_id}", result_type=ApplicationResult
    )
    await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

    # state=AWAITING_APPROVAL 이 persist 된 시점과 승인 nonce 가 발급되는 시점(request_approval
    # activity 완료) 사이에 짧은 간격이 있다 — has_pending=True 가 될 때까지 기다린다.
    async def _pending_true():
        resp = await ac.get(f"/applications/{application_id}/pending")
        assert resp.status_code == 200
        return resp if resp.json()["has_pending"] else None

    pending = await _poll(_pending_true)
    assert pending is not None, "승인 대기 상태에 도달하지 않았다"
    body = pending.json()
    assert body["job_url"] == JOB_URL
    assert body["resume_url"]

    approved = await ac.post(f"/applications/{application_id}/approve", json={})
    assert approved.status_code == 202
    await handle.result()

    async def _pending_false():
        resp = await ac.get(f"/applications/{application_id}/pending")
        return resp if resp.json()["has_pending"] is False else None

    after = await _poll(_pending_false)
    assert after is not None
    assert after.json()["has_pending"] is False


async def test_apply_by_url_endpoint_starts_workflow():
    """`apply_by_url`은 wanted 플랫폼만 허용한다 — fixture 등록은 wanted 가 아니므로

    unsupported_platform 만 확인한다(플랫폼 확인 자체가 이 엔드포인트가 실제로 `apply_intake.
    apply_by_url`을 호출한다는 증거).
    """
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        h = Harness()
        app.state.container = h.container()
        app.state.temporal_client = env.client
        async with (
            _Workers(env.client, h),
            httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
        ):
            resp = await ac.post("/applications/apply-by-url", json={"url": JOB_URL})
            assert resp.status_code == 202
            body = resp.json()
            assert body["outcome"] == "unsupported_platform"
            assert body["application_id"] is None


async def test_pending_decision_unknown_application_returns_404(client):
    ac, _env, _h = client
    resp = await ac.get("/applications/does-not-exist/pending")
    assert resp.status_code == 404
