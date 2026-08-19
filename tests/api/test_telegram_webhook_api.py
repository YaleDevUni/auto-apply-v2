"""POST /telegram/webhook — 콜백 → signal 변환을 실제 워크플로우로 검증한다.

TelegramNotifier 고유 동작(콜백 데이터 형식, chat 브로드캐스트)은
tests/adapters/test_telegram_notifier.py 가 이미 커버한다. 여기서는 웹훅 라우트가 nonce 를
올바르게 소비하고 signal 로 바꾸는지만 본다 — 그래서 Notifier 대역은 `ConsoleNotifier` 로
충분하다 (둘 다 같은 nonce 계약을 구현한다, §11.5).
"""

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment

from auto_apply.api.main import app
from auto_apply.config import Settings
from auto_apply.contracts.dto import ApplicationResult
from auto_apply.domain.enums import ApplicationState
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow
from tests.conftest import Harness
from tests.workflows.test_application import APP_ID, _cmd, _wait_state, _Workers

pytestmark = pytest.mark.integration

ALLOWED_CHAT_ID = 42


def _callback_body(action: str, application_id: str, nonce: str, chat_id: int) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cbq_1",
            "from": {"id": chat_id},
            "data": f"{action}:{application_id}:{nonce}",
        }
    }


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


@pytest.fixture
async def client(env: WorkflowEnvironment):
    h = Harness()
    app.state.container = h.container(
        settings=Settings(
            notifier="telegram",
            telegram_allowed_chat_ids=str(ALLOWED_CHAT_ID),
            storage="memory",
            llm_provider="stub",
        )
    )
    app.state.temporal_client = env.client
    async with (
        _Workers(env.client, h),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac, env, h


async def _awaiting_approval_with_nonce(env: WorkflowEnvironment, h: Harness):
    handle = await env.client.start_workflow(
        ApplicationWorkflow.run,
        _cmd(),
        id=f"application-{APP_ID}",
        task_queue=QUEUE_DEFAULT,
        result_type=ApplicationResult,
    )
    await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
    assert h.notifier is not None
    # 상태가 AWAITING_APPROVAL 로 보이는 시점과 request_approval activity(=nonce 발급)가
    # 끝나는 시점 사이에는 미세한 간극이 있다 — self._state 는 activity 실행 전에 바뀐다.
    nonce = None
    for _ in range(100):
        nonce = h.notifier.peek_nonce(APP_ID)  # type: ignore[attr-defined]
        if nonce is not None:
            break
        await asyncio.sleep(0.05)
    assert nonce is not None, "request_approval activity 가 끝나지 않았다"
    return handle, nonce


async def test_approve_callback_from_allowed_chat_completes(client):
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("a", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200

    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_reject_callback_rejects_application(client):
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("r", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200

    result = await handle.result()
    assert result.state is ApplicationState.REJECTED


async def test_replayed_callback_is_ignored(client):
    """같은 nonce 로 두 번 클릭 — 첫 번째만 반영되고 두 번째는 조용히 무시된다."""
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    body = _callback_body("a", APP_ID, nonce, ALLOWED_CHAT_ID)
    first = await ac.post("/telegram/webhook", json=body)
    second = await ac.post("/telegram/webhook", json=body)
    assert first.status_code == 200
    assert second.status_code == 200

    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_callback_from_unknown_chat_is_ignored(client):
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("a", APP_ID, nonce, chat_id=99999)
    )
    assert resp.status_code == 200

    view = await handle.query(ApplicationWorkflow.state)
    assert view.state is ApplicationState.AWAITING_APPROVAL, (
        "허용되지 않은 chat 의 클릭은 무시돼야 한다"
    )


async def test_webhook_disabled_when_notifier_is_not_telegram(client):
    ac, _env, h = client
    app.state.container = h.container(settings=Settings(notifier="console"))

    resp = await ac.post("/telegram/webhook", json={"callback_query": {}})

    assert resp.status_code == 404
