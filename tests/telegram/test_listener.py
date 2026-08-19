"""telegram/listener.py — 네트워크 없이 getUpdates 호출 형태만 검증한다.

콜백을 실제로 처리해 워크플로우에 signal 하는 부분은 telegram/bridge.py 의 몫이고,
그건 tests/api/test_telegram_webhook_api.py 가 실제 Temporal 로 검증한다. 여기서는
"롱폴링 요청이 offset/allowed_updates 를 올바르게 실어 보내는지"만 본다.
"""

import httpx

from auto_apply.telegram.listener import _fetch_updates


async def test_fetch_updates_sends_offset_and_restricts_to_callback_query():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 42}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await _fetch_updates(http, "token", offset=41)

    assert updates == [{"update_id": 42}]
    assert "getUpdates" in captured["url"]
    assert captured["params"]["offset"] == "41"
    assert captured["params"]["allowed_updates"] == '["callback_query"]'


async def test_fetch_updates_omits_offset_when_none():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        # 예외 없이 끝나면 충분하다 — offset=None 을 그대로 보내면 httpx 가 잘못 인코딩한다
        updates = await _fetch_updates(http, "token", offset=None)

    assert updates == []
