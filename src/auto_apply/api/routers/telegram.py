"""POST /telegram/webhook — Bot 진입점 (§6, §7).

이 파일은 콜백 데이터를 해석해서 signal 로 바꾸는 얇은 어댑터다. 벤더 SDK(`telegram`) 나
구체 어댑터(`TelegramNotifier`)를 import 하지 않는다 — 봇 토큰/전송 방식을 아는 곳은
`adapters/notifier/telegram.py` 하나뿐이어야 한다 (§11.3, §11.6). 여기서는 `Notifier`
port 의 `consume_ticket`(nonce 검증)과 `notify`(결과 알림)만 쓴다.

콜백 데이터 형식은 `TelegramNotifier.request_decision` 이 만든다:
`"{a|r}:{application_id}:{nonce}"`.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from temporalio.service import RPCError

from auto_apply.api.deps import ContainerDep, TemporalClientDep
from auto_apply.contracts.dto import ApproveSignal, NotifyEvent, RejectSignal
from auto_apply.workflows.application import ApplicationWorkflow

router = APIRouter(tags=["telegram"])

_ACTIONS = {"a": "승인", "r": "거절"}


def _parse_callback_data(data: str) -> tuple[str, str, str]:
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] not in _ACTIONS:
        raise HTTPException(400, "malformed callback_data")
    action, application_id, nonce = parts
    return action, application_id, nonce


@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request, c: ContainerDep, client: TemporalClientDep
) -> dict[str, Any]:
    if c.settings.notifier != "telegram":
        raise HTTPException(404, "telegram notifier is not enabled")

    body = await request.json()
    callback = body.get("callback_query")
    if callback is None:
        return {"ok": True}  # 이 봇이 다루는 유일한 인바운드는 승인/거절 버튼이다 — 나머지는 무시

    from_id = callback.get("from", {}).get("id")
    if from_id not in c.settings.allowed_chat_ids:
        # 허용되지 않은 사용자 — 이 봇은 실제 제출 권한을 가진 콘솔이다 (§6)
        return {"ok": True}

    action, application_id, nonce = _parse_callback_data(callback.get("data", ""))

    if not await c.notifier.consume_ticket(application_id, nonce):
        await c.notifier.notify(
            NotifyEvent(
                kind="STALE_DECISION",
                application_id=application_id,
                message="이미 처리됐거나 오래된 버튼입니다.",
            )
        )
        return {"ok": True}

    wf_id = f"application-{application_id}"
    decided_by = str(from_id)
    try:
        handle = client.get_workflow_handle(wf_id)
        if action == "a":
            await handle.signal(ApplicationWorkflow.approve, ApproveSignal(decided_by=decided_by))
        else:
            await handle.signal(ApplicationWorkflow.reject, RejectSignal(decided_by=decided_by))
    except RPCError as e:
        raise HTTPException(404, f"application {application_id} not found") from e

    await c.notifier.notify(
        NotifyEvent(
            kind="DECISION_RECORDED",
            application_id=application_id,
            message=f"{_ACTIONS[action]} 처리됐습니다.",
        )
    )
    return {"ok": True}
