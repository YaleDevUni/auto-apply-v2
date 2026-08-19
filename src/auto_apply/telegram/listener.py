"""텔레그램 롱폴링 리스너 — 공인 HTTPS 웹훅이 없는 로컬 개발 환경용.

`getUpdates` 롱폴링은 인바운드 접속을 받을 필요가 없다(방화벽/ngrok 불필요). 처리 로직은
`telegram/bridge.py` 를 `api/routers/telegram.py`(웹훅)와 그대로 공유한다 — 콜백을 받아오는
통로만 다르다. 공인 URL 이 있는 배포 환경에서는 웹훅을 쓰고 이 리스너는 로컬 전용으로 둔다.

`python-telegram-bot`(`Bot.get_updates`)을 쓰지 않고 raw HTTP GET 을 직접 부른다 — Bot API
JSON 을 그대로 받아야 `telegram/bridge.py` 에 변환 없이 넘길 수 있고, `Update` 객체의
`from_user`↔`"from"` 필드 리네이밍 같은 SDK 쪽 변환을 신경 쓸 필요가 없어진다. `TelegramNotifier`
가 갖고 있는 `Bot`(발신 전용)과는 별개의 연결이다 — 인바운드 폴링은 발신과 무관한 관심사다.

offset 을 재시작 사이에 영속화하지 않는다 — nonce 가 1회성 소비라 같은 콜백이 재전달돼도
`consume_ticket` 이 조용히 걸러낸다(§6). 그래서 정확성이 아니라 "재시작 직후 오래된 콜백을
한 번 더 훑는" 정도의 비용만 남는다.
"""

import asyncio
from typing import Any

import httpx
import structlog

from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings
from auto_apply.telegram.bridge import MalformedCallback, handle_callback_query
from auto_apply.temporal_config import DATA_CONVERTER

log = structlog.get_logger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT_S = 25


async def _fetch_updates(
    http: httpx.AsyncClient, token: str, offset: int | None
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "timeout": _POLL_TIMEOUT_S,
        "allowed_updates": '["callback_query"]',
    }
    if offset is not None:
        params["offset"] = offset
    resp = await http.get(
        _API.format(token=token, method="getUpdates"),
        params=params,
        timeout=httpx.Timeout(connect=8, read=_POLL_TIMEOUT_S + 8, write=8, pool=8),
    )
    resp.raise_for_status()
    result = resp.json().get("result", [])
    return list(result)


async def main() -> None:
    from temporalio.client import Client

    cfg = load_settings()
    if cfg.notifier != "telegram":
        raise SystemExit("NOTIFIER=telegram 이어야 리스너를 돌릴 수 있다")
    if not cfg.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 필요하다")

    container = build_container(cfg)
    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )
    log.info("telegram.listener.start")

    offset: int | None = None
    async with httpx.AsyncClient() as http:
        while True:
            try:
                updates = await _fetch_updates(http, cfg.telegram_bot_token, offset)
            except httpx.HTTPError as e:
                log.warning("telegram.listener.poll_failed", error=str(e))
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = int(update["update_id"]) + 1
                callback = update.get("callback_query")
                if not callback:
                    continue
                try:
                    outcome = await handle_callback_query(callback, container, client)
                except MalformedCallback:
                    log.warning("telegram.listener.malformed_callback", data=callback)
                    continue
                log.info(
                    "telegram.listener.callback", handled=outcome.handled, reason=outcome.reason
                )


if __name__ == "__main__":
    asyncio.run(main())
