"""POST /telegram/webhook — 콜백 → signal 변환을 실제 워크플로우로 검증한다.

TelegramNotifier 고유 동작(콜백 데이터 형식, chat 브로드캐스트)은
tests/adapters/test_telegram_notifier.py 가 이미 커버한다. 여기서는 웹훅 라우트가 콜백을
signal 로 바꾸고, nonce 검증(워크플로우가 한다, ports/notifier.py 참고)이 실제로 오래된/
위조된 버튼을 걸러내는지를 본다.
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
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.repair import AutomationRepairWorkflow
from tests.conftest import Harness
from tests.workflows.test_application import APP_ID, _cmd, _wait_state, _Workers
from tests.workflows.test_repair import _FIXED_DIFF, FORM_HASH, PLATFORM, _req

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
    # (nonce 는 이제 워크플로우 안에만 있다 — h.notifier 는 테스트가 옆에서 훔쳐본 값이다.)
    nonce = None
    for _ in range(100):
        nonce = h.notifier.last_ticket.get(APP_ID)
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


async def test_stale_nonce_from_different_process_is_rejected(client):
    """회귀 테스트: nonce 검증을 어댑터 메모리에 뒀을 때, 발급 프로세스(worker)와 검증

    프로세스(webhook 서버)가 갈라지면 진짜 nonce 를 보내도 항상 거부됐다(라이브
    스모크테스트에서 실측). 워크플로우가 검증하는 지금은 "가짜/오래된 nonce" 만 거부돼야
    하고, 그 경우 signal 자체가 조용히 무시돼 워크플로우는 계속 대기해야 한다.
    """
    ac, env, h = client
    handle, _real_nonce = await _awaiting_approval_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook",
        json=_callback_body("a", APP_ID, "totally-different-nonce", ALLOWED_CHAT_ID),
    )
    assert resp.status_code == 200  # signal 배선 자체는 성공 — 워크플로우가 내용을 걸러낸다

    view = await handle.query(ApplicationWorkflow.state)
    assert view.state is ApplicationState.AWAITING_APPROVAL, "위조/오래된 nonce 는 무시돼야 한다"


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


# ──────────────── REVISE(수정요청): 버튼 → scope 선택 → ForceReply 답장 ────────────────
def _revise_start_body(application_id: str, nonce: str, chat_id: int) -> dict[str, Any]:
    return _callback_body("v", application_id, nonce, chat_id)


def _scope_choice_body(application_id: str, scope: str, nonce: str, chat_id: int) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cbq_2",
            "from": {"id": chat_id},
            "data": f"vs:{application_id}:{scope}:{nonce}",
        }
    }


def _revise_reply_body(prompt_text: str, feedback: str, chat_id: int) -> dict[str, Any]:
    return {
        "message": {
            "from": {"id": chat_id},
            "text": feedback,
            "reply_to_message": {"text": prompt_text},
        }
    }


async def test_revise_button_flow_reaches_awaiting_approval_again(client):
    """v 버튼 → scope 선택 → ForceReply 답장 — 전 구간이 signal 로 이어져 재생성된다."""
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    v_resp = await ac.post(
        "/telegram/webhook", json=_revise_start_body(APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert v_resp.status_code == 200

    vs_resp = await ac.post(
        "/telegram/webhook", json=_scope_choice_body(APP_ID, "specific", nonce, ALLOWED_CHAT_ID)
    )
    assert vs_resp.status_code == 200

    prompt_text = f"[revise:{APP_ID}:{nonce}:specific]"
    reply_resp = await ac.post(
        "/telegram/webhook",
        json=_revise_reply_body(prompt_text, "자기소개를 더 짧게", ALLOWED_CHAT_ID),
    )
    assert reply_resp.status_code == 200
    assert reply_resp.json()["handled"] is True

    # 재생성이 끝나면 새 nonce 로 다시 승인을 요청한다 — 두 번째 nonce 가 뜰 때까지 기다린다
    second_nonce = None
    for _ in range(300):
        candidate = h.notifier.last_ticket.get(APP_ID)
        if candidate is not None and candidate != nonce:
            second_nonce = candidate
            break
        await asyncio.sleep(0.05)
    assert second_nonce is not None, "REVISE 이후 재승인 요청이 안 왔다"

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("a", APP_ID, second_nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200
    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_revise_cancel_leaves_original_decision_buttons_usable(client):
    """scope 선택 화면에서 취소를 누르면 signal 없이 멈추고, 원래 nonce 로 그대로 승인할 수 있다."""
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    v_resp = await ac.post(
        "/telegram/webhook", json=_revise_start_body(APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert v_resp.status_code == 200

    vc_resp = await ac.post(
        "/telegram/webhook", json=_callback_body("vc", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert vc_resp.status_code == 200
    assert vc_resp.json()["handled"] is True

    view = await handle.query(ApplicationWorkflow.state)
    assert view.state is ApplicationState.AWAITING_APPROVAL

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("a", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200
    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_revise_feedback_prompt_cancel_leaves_original_decision_buttons_usable(client):
    """ForceReply 로 자유 텍스트를 입력하는 단계까지 간 뒤에도 취소하면 signal 없이 멈추고,

    원래 nonce 로 그대로 승인할 수 있다 — `send_feedback_prompt`가 뒤이어 보내는 취소
    버튼이 scope 선택 화면의 `vc`와 같은 콜백을 재사용하기 때문이다.
    """
    ac, env, h = client
    handle, nonce = await _awaiting_approval_with_nonce(env, h)

    v_resp = await ac.post(
        "/telegram/webhook", json=_revise_start_body(APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert v_resp.status_code == 200

    vs_resp = await ac.post(
        "/telegram/webhook", json=_scope_choice_body(APP_ID, "specific", nonce, ALLOWED_CHAT_ID)
    )
    assert vs_resp.status_code == 200

    vc_resp = await ac.post(
        "/telegram/webhook", json=_callback_body("vc", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert vc_resp.status_code == 200
    assert vc_resp.json()["handled"] is True

    view = await handle.query(ApplicationWorkflow.state)
    assert view.state is ApplicationState.AWAITING_APPROVAL

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("a", APP_ID, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200
    result = await handle.result()
    assert result.state is ApplicationState.COMPLETED


async def test_revise_reply_without_matching_tag_is_routed_to_chat_agent(client):
    """태그가 안 붙은 일반 답장(REVISE 프롬프트가 아닌 메시지에 대한 답)은 더 이상 무시되지

    않는다 — telegram/agent.py 의 채팅 에이전트로 넘어간다. 이 fixture 의 StubLLM 은 resume
    payload 큐를 쓰므로(AgentStep 스키마와 안 맞는다) 여기서는 "채팅 에이전트가 스키마 위반을
    안 죽고 사과 메시지로 흡수하는지"만 본다 — 실제 도구 선택 로직은 tests/telegram/test_agent.py.
    워크플로우는 이 대화와 무관하게 그대로 AWAITING_APPROVAL 이어야 한다(자연어가 승인을
    대신하지 않는다, CLAUDE.md 절대규칙 4).
    """
    ac, env, h = client
    handle, _nonce = await _awaiting_approval_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook",
        json=_revise_reply_body("아무 태그도 없는 메시지", "오늘 지원 몇 건이야?", ALLOWED_CHAT_ID),
    )
    assert resp.status_code == 200
    assert resp.json()["handled"] is True

    view = await handle.query(ApplicationWorkflow.state)
    assert view.state is ApplicationState.AWAITING_APPROVAL

    assert h.notifier is not None
    assert any(e.kind == "CHAT" for e in h.notifier.notified)


async def test_chat_agent_disabled_setting_falls_back_to_ignoring(env: WorkflowEnvironment):
    """telegram_chat_agent_enabled=False 면 예전 동작(태그 없는 자유 텍스트는 조용히 무시)으로

    정확히 되돌아간다 — 재배포 없이 끌 수 있는 손잡이(config.py 참고).
    """
    h = Harness()
    app.state.container = h.container(
        settings=Settings(
            notifier="telegram",
            telegram_allowed_chat_ids=str(ALLOWED_CHAT_ID),
            storage="memory",
            llm_provider="stub",
            telegram_chat_agent_enabled=False,
        )
    )
    app.state.temporal_client = env.client
    async with (
        _Workers(env.client, h),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        _handle, _nonce = await _awaiting_approval_with_nonce(env, h)

        resp = await ac.post(
            "/telegram/webhook",
            json=_revise_reply_body(
                "아무 태그도 없는 메시지", "오늘 지원 몇 건이야?", ALLOWED_CHAT_ID
            ),
        )
        assert resp.status_code == 200
        assert resp.json()["handled"] is False

        assert h.notifier is not None
        assert not any(e.kind == "CHAT" for e in h.notifier.notified)


# ──────────────── recipe 승격 승인(pa/pr, §2.4) — AutomationRepairWorkflow 라우팅 ────────────────
@pytest.fixture
async def repair_client(env: WorkflowEnvironment):
    """`client`와 별도 fixture다 — repair 는 `repair_diff_payloads`가 채워진 Harness 가 필요해서

    기본 `client`(빈 payload, 곧바로 포기하는 경로용)를 공유할 수 없다.
    """
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
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
        # _Workers(tests.workflows.test_application) 가 QUEUE_AI 에 AutomationRepairWorkflow 도
        # 이미 등록해 둔다 — application-*/repair-* 를 구분하지 않는 같은 워커 셋을 그대로 쓴다.
        _Workers(env.client, h),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac, env, h


async def _repairing_with_nonce(env: WorkflowEnvironment, h: Harness):
    key = f"{PLATFORM}-{FORM_HASH}"
    handle = await env.client.start_workflow(
        AutomationRepairWorkflow.run,
        _req(),
        id=f"repair-{PLATFORM}-{FORM_HASH}",
        task_queue=QUEUE_AI,
    )
    assert h.notifier is not None
    nonce = None
    for _ in range(300):
        nonce = h.notifier.last_ticket.get(key)
        if nonce is not None:
            break
        await asyncio.sleep(0.05)
    assert nonce is not None, "recipe 승격 승인 요청이 안 왔다"
    return handle, key, nonce


async def test_repair_promotion_approve_callback_promotes_recipe(repair_client):
    ac, env, h = repair_client
    handle, key, nonce = await _repairing_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("pa", key, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200

    result = await handle.result()
    assert result.promoted is True


async def test_repair_promotion_reject_callback_leaves_candidate_unpromoted(repair_client):
    ac, env, h = repair_client
    handle, key, nonce = await _repairing_with_nonce(env, h)

    resp = await ac.post(
        "/telegram/webhook", json=_callback_body("pr", key, nonce, ALLOWED_CHAT_ID)
    )
    assert resp.status_code == 200

    result = await handle.result()
    assert result.promoted is False
