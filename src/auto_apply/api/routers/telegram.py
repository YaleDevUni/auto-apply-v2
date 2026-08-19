"""POST /telegram/webhook — Bot 진입점 (§6, §7).

공인 HTTPS 로 배포됐을 때 쓰는 경로다. 로컬 개발처럼 공인 URL 이 없으면 이 경로에
아무도 닿지 못한다 — 그때는 `telegram/listener.py`(롱폴링)를 대신 쓴다. 두 경로 모두
콜백 처리 로직은 `telegram/bridge.py` 하나를 공유한다.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from auto_apply.api.deps import ContainerDep, TemporalClientDep
from auto_apply.telegram.bridge import MalformedCallback, handle_callback_query, handle_message

router = APIRouter(tags=["telegram"])


@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request, c: ContainerDep, client: TemporalClientDep
) -> dict[str, Any]:
    if c.settings.notifier != "telegram":
        raise HTTPException(404, "telegram notifier is not enabled")

    body = await request.json()
    try:
        if (callback := body.get("callback_query")) is not None:
            outcome = await handle_callback_query(callback, c, client)
        elif (message := body.get("message")) is not None:
            outcome = await handle_message(message, c, client)
        else:
            # 이 봇이 다루는 인바운드는 승인/거절/수정요청 버튼과 REVISE 답장뿐이다 — 나머지는 무시
            return {"ok": True}
    except MalformedCallback as e:
        raise HTTPException(400, "malformed callback_data") from e
    return {"ok": True, "handled": outcome.handled, "reason": outcome.reason}
