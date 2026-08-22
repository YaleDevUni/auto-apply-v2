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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from auto_apply.bootstrap import Container, build_container
from auto_apply.config import load_settings
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.process_alerts import notify_safely, run_guarded
from auto_apply.telegram.bridge import (
    CallbackOutcome,
    MalformedCallback,
    handle_callback_query,
    handle_message,
    inbound_failure_message,
)
from auto_apply.temporal_config import DATA_CONVERTER

if TYPE_CHECKING:
    from temporalio.client import Client

log = structlog.get_logger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT_S = 25


async def _fetch_updates(
    http: httpx.AsyncClient, token: str, offset: int | None
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "timeout": _POLL_TIMEOUT_S,
        "allowed_updates": '["callback_query","message"]',
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

    async def on_error(update: dict[str, Any], error: Exception) -> None:
        await notify_safely(
            container.notifier,
            NotifyEvent(kind="TELEGRAM_INBOUND_FAILED", message=inbound_failure_message(error)),
        )

    async with httpx.AsyncClient() as http:
        while True:
            try:
                updates = await _fetch_updates(http, cfg.telegram_bot_token, offset)
            except httpx.HTTPError as e:
                log.warning("telegram.listener.poll_failed", error=str(e))
                await asyncio.sleep(5)
                continue

            new_offset = await _process_updates(
                updates, lambda u: _dispatch(u, container, client), on_error=on_error
            )
            if new_offset is not None:
                offset = new_offset


async def _process_updates(
    updates: list[dict[str, Any]],
    dispatch: Callable[[dict[str, Any]], Awaitable[CallbackOutcome | None]],
    *,
    on_error: Callable[[dict[str, Any], Exception], Awaitable[None]] | None = None,
) -> int | None:
    """update 를 순서대로 처리한다. 하나가 실패해도 나머지는 계속 처리하고 offset 은 넘긴다.

    회귀 테스트로 실측: 리스너가 꺼져 있던 동안 눌린 버튼의 callback_query 는 재기동 후
    `answerCallbackQuery`가 "Query is too old" `BadRequest`를 던진다. `MalformedCallback`만
    잡던 코드는 이 예외로 리스너 프로세스 자체가 죽었고, offset 을 재시작 사이에 영속화하지
    않는 설계(모듈 docstring)라 다음 기동에서 같은 update 를 또 받아 똑같이 죽는 무한
    크래시루프가 됐다. 모듈 docstring이 전제한 "재처리는 비용만 남는다"가 성립하려면 여기서
    어떤 예외가 나도 이 프로세스는 살아 있어야 한다.

    다만 "살아 있다"와 "조용하다"는 다르다 — 버튼을 누른 사람 입장에서 dispatch 실패는
    `_notify_signal_failed`(bridge.py)가 다루는 "signal 이 안 닿았다"와 구분이 안 되는 무응답이다.
    그래서 로그만 남기지 않고 `on_error` 로 사람에게도 알린다.
    """
    offset: int | None = None
    for update in updates:
        offset = int(update["update_id"]) + 1
        try:
            outcome = await dispatch(update)
        except MalformedCallback as e:
            log.warning("telegram.listener.malformed_callback", data=str(e))
            continue
        except Exception as e:
            log.exception("telegram.listener.dispatch_failed", update_id=update.get("update_id"))
            if on_error is not None:
                await on_error(update, e)
            continue
        if outcome is None:
            continue
        log.info("telegram.listener.callback", handled=outcome.handled, reason=outcome.reason)
    return offset


async def _dispatch(
    update: dict[str, Any], container: Container, client: "Client"
) -> CallbackOutcome | None:
    if (callback := update.get("callback_query")) is not None:
        return await handle_callback_query(callback, container, client)
    if (message := update.get("message")) is not None:
        return await handle_message(message, container, client)
    return None


if __name__ == "__main__":
    # 리스너가 죽으면 승인 버튼이 조용히 안 먹는다 — 워크플로우는 approval_timeout 이 지나서야
    # EXPIRED 로 끝난다 (§ process_alerts.py).
    asyncio.run(run_guarded("telegram listener", main))
