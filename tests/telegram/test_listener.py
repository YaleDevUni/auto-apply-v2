"""telegram/listener.py — 네트워크 없이 getUpdates 호출 형태만 검증한다.

콜백을 실제로 처리해 워크플로우에 signal 하는 부분은 telegram/bridge.py 의 몫이고,
그건 tests/api/test_telegram_webhook_api.py 가 실제 Temporal 로 검증한다. 여기서는
"롱폴링 요청이 offset/allowed_updates 를 올바르게 실어 보내는지"만 본다.
"""

import httpx

from auto_apply.telegram.bridge import CallbackOutcome, MalformedCallback
from auto_apply.telegram.listener import _fetch_updates, _process_updates


async def test_fetch_updates_sends_offset_and_restricts_to_callback_query_and_message():
    """`message` 도 받는다 — REVISE ForceReply 답장이 이 타입으로 온다(telegram/bridge.py)."""
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
    assert captured["params"]["allowed_updates"] == '["callback_query","message"]'


async def test_fetch_updates_omits_offset_when_none():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        # 예외 없이 끝나면 충분하다 — offset=None 을 그대로 보내면 httpx 가 잘못 인코딩한다
        updates = await _fetch_updates(http, "token", offset=None)

    assert updates == []


async def test_process_updates_continues_past_a_dispatch_that_raises():
    """회귀 테스트: 만료된 callback_query 에 answerCallbackQuery 를 호출하면 텔레그램이

    `BadRequest`를 던지는 걸 라이브에서 봤다(listener 가 꺼져 있던 동안 눌린 버튼). 예전
    코드는 `MalformedCallback`만 잡아서 이런 예외가 리스너 프로세스 자체를 죽였고, offset 을
    재시작 사이에 영속화하지 않아 다음 기동에서 같은 update 를 또 받아 무한 크래시루프가 됐다.
    """
    calls: list[int] = []

    async def dispatch(update: dict[str, object]) -> CallbackOutcome | None:
        calls.append(int(update["update_id"]))
        if update["update_id"] == 1:
            raise RuntimeError("Query is too old and response timeout expired")
        return CallbackOutcome(handled=True)

    offset = await _process_updates([{"update_id": 1}, {"update_id": 2}], dispatch)

    assert calls == [1, 2]  # update 1 이 raise 해도 update 2 는 계속 처리된다
    assert offset == 3  # 실패한 update 도 offset 은 넘어가서 다음 poll 에서 다시 안 받는다


async def test_process_updates_still_advances_offset_on_malformed_callback():
    async def dispatch(update: dict[str, object]) -> CallbackOutcome | None:
        raise MalformedCallback("bogus")

    offset = await _process_updates([{"update_id": 5}], dispatch)

    assert offset == 6
