"""telegram/bridge.py 의 ca/cr(체크포인트 승인/거절) 분기 (§ supervised-checkpoint-design).

다른 액션과 달리 워크플로우 signal 을 안 탄다 — CheckpointWaiter 는 activity 안에서
CheckpointStore 를 폴링하며 기다리므로, 콜백은 `client`(Temporal)를 건드리지 않고
`c.checkpoint_store.record_decision` 을 직접 부른다. 그래서 이 테스트는 실제 Temporal 워크플로우
없이 `Harness().container()` 만으로 검증할 수 있다.
"""

from typing import Any

from auto_apply.config import Settings
from auto_apply.telegram.bridge import handle_callback_query
from tests.conftest import Harness

ALLOWED_CHAT_ID = 42


def _callback(action: str, application_id: str, nonce: str) -> dict[str, Any]:
    return {
        "id": "cbq_1",
        "from": {"id": ALLOWED_CHAT_ID},
        "data": f"{action}:{application_id}:{nonce}",
    }


def _container(h: Harness) -> Any:
    return h.container(
        settings=Settings(
            notifier="telegram",
            telegram_allowed_chat_ids=str(ALLOWED_CHAT_ID),
            storage="memory",
            llm_provider="stub",
        )
    )


async def test_checkpoint_approve_records_decision_without_touching_temporal():
    h = Harness()
    c = _container(h)

    outcome = await handle_callback_query(
        _callback("ca", "app_1", "nonce_1"),
        c,
        client=None,  # type: ignore[arg-type]
    )

    assert outcome.handled
    assert await c.checkpoint_store.get_decision("nonce_1") is True


async def test_checkpoint_reject_records_decision():
    h = Harness()
    c = _container(h)

    outcome = await handle_callback_query(
        _callback("cr", "app_1", "nonce_1"),
        c,
        client=None,  # type: ignore[arg-type]
    )

    assert outcome.handled
    assert await c.checkpoint_store.get_decision("nonce_1") is False


async def test_checkpoint_decision_notifies_the_chat():
    h = Harness()
    c = _container(h)

    await handle_callback_query(_callback("ca", "app_1", "nonce_1"), c, client=None)  # type: ignore[arg-type]

    assert any(
        e.kind == "DECISION_RECORDED" and "승인" in e.message
        for e in h.notifier.notified  # type: ignore[union-attr]
    )
