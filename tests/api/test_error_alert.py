"""api/main.py 의 미처리 예외 처리기 — 500 을 내기 전에 사람에게 알린다.

이 API 는 컨트롤 플레인이라 호출자가 늘 사람인 건 아니다. 특히 `POST /telegram/webhook` 은
텔레그램 서버가 부르므로 500 이 나면 버튼을 누른 사람에게는 그냥 무응답으로 보인다 —
로그를 열지 않는 한 아무도 모르는 지점이라 알림을 건다.

`test_recipes_api.py`와 같은 이유로 Temporal 없이 컨테이너만 갈아끼운다.
"""

from dataclasses import replace

import httpx
import pytest
from httpx import ASGITransport

from auto_apply.api.main import app
from tests.conftest import Harness
from tests.ports.test_notifier_contract import RecordingNotifier


class _BrokenRecipeSource:
    """라우터가 예상하지 못한 예외 — 도메인 예외(PolicyViolation→409)가 아니라 진짜 사고."""

    async def versions(self, platform: str) -> list[object]:
        raise RuntimeError("DB 연결이 끊겼다")


@pytest.fixture
async def client():
    notifier = RecordingNotifier()
    base = Harness().container()
    app.state.container = replace(
        base,
        notifier=notifier,
        recipes=_BrokenRecipeSource(),
        # 웹훅 라우트는 notifier 설정이 telegram 이 아니면 404 로 먼저 끊는다 — 예외 처리기까지
        # 가려면 켜져 있어야 한다.
        settings=base.settings.model_copy(update={"notifier": "telegram"}),
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, notifier


async def test_unhandled_error_alerts_and_returns_500(client):
    ac, notifier = client

    resp = await ac.get("/recipes/wanted")

    assert resp.status_code == 500
    assert [e.kind for e in notifier.events] == ["API_ERROR"]
    assert "/recipes/wanted" in notifier.events[0].message
    assert "RuntimeError" in notifier.events[0].message


async def test_telegram_webhook_failure_speaks_to_the_person_who_pressed_the_button(client):
    ac, notifier = client

    resp = await ac.post("/telegram/webhook", content=b"not-json")

    assert resp.status_code == 500
    assert [e.kind for e in notifier.events] == ["API_ERROR"]
    assert "다시 시도해주세요" in notifier.events[0].message
