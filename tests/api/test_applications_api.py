"""§7 FastAPI 지원 엔드포인트 — 실제 Temporal(time-skipping) 서버에 붙여서 검증한다.

라우터는 얇은 signal/query 배선이 전부이므로, 페이크 Temporal 클라이언트를 만드는 대신
기존 워크플로우 테스트와 같은 real time-skipping 서버 + 워커를 재사용한다
(tests/workflows/test_application.py 의 `_Workers`/`Harness` 패턴).
"""

import asyncio

import httpx
import pytest
from httpx import ASGITransport
from temporalio.testing import WorkflowEnvironment

from auto_apply.api.main import app
from auto_apply.contracts.dto import ApplicationResult
from auto_apply.domain.enums import ApplicationState
from auto_apply.temporal_config import DATA_CONVERTER
from tests.conftest import JOB_URL, Harness
from tests.workflows.test_application import _wait_state, _Workers

pytestmark = pytest.mark.integration


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


@pytest.fixture
async def client(env: WorkflowEnvironment):
    h = Harness()
    app.state.container = h.container()
    app.state.temporal_client = env.client
    async with (
        _Workers(env.client, h),
        httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac, env, h


async def test_start_then_status_reaches_awaiting_approval(client):
    ac, _env, _h = client

    resp = await ac.post("/applications", json={"user_id": "u1", "job_url": JOB_URL})
    assert resp.status_code == 202
    body = resp.json()
    application_id = body["application_id"]
    assert body["workflow_id"] == f"application-{application_id}"

    # DB projection 이 아니라 워크플로우 query 가 상태의 원본이다 (§4.1)
    got = None
    for _ in range(200):
        got = await ac.get(f"/applications/{application_id}")
        if got.json()["state"] == ApplicationState.AWAITING_APPROVAL.value:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("승인 대기 상태에 도달하지 않았다")
    assert got.json()["history"], "history 는 persist_state 가 쌓은 projection 이어야 한다"


async def test_approve_via_api_completes_dry_run(client):
    ac, env, _h = client
    resp = await ac.post("/applications", json={"user_id": "u1", "job_url": JOB_URL})
    application_id = resp.json()["application_id"]
    # result_type 을 주지 않으면 워크플로우 결과가 dict 로 온다 — 타입 있는 handle 을 새로 얻는다
    handle = env.client.get_workflow_handle(
        f"application-{application_id}", result_type=ApplicationResult
    )
    await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

    approved = await ac.post(f"/applications/{application_id}/approve", json={})
    assert approved.status_code == 202

    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_reject_via_api(client):
    ac, env, _h = client
    resp = await ac.post("/applications", json={"user_id": "u1", "job_url": JOB_URL})
    application_id = resp.json()["application_id"]
    handle = env.client.get_workflow_handle(
        f"application-{application_id}", result_type=ApplicationResult
    )
    await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

    rejected = await ac.post(
        f"/applications/{application_id}/reject", json={"reason": "회사 부적합"}
    )
    assert rejected.status_code == 202

    result = await handle.result()
    assert result.state is ApplicationState.REJECTED
    assert result.reason == "회사 부적합"


async def test_unknown_application_returns_404(client):
    ac, _env, _h = client
    resp = await ac.get("/applications/does-not-exist")
    assert resp.status_code == 404
    resp = await ac.post("/applications/does-not-exist/cancel")
    assert resp.status_code == 404
