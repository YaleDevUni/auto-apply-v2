"""승인 콜백 → signal 변환. 웹훅(`api/routers/telegram.py`)과 롱폴링(`telegram/listener.py`)

이 같은 로직을 공유한다 (ARCHITECTURE.md §8 이 계획한 telegram/ 패키지의 자리). 인바운드
경로가 둘이어도 "콜백을 어떻게 처리하는가"는 하나여야 어긋나지 않는다.

nonce 검증은 여기서 하지 않는다 — `ApplicationWorkflow` 의 signal 핸들러가 발급된 nonce 를
직접 들고 검증한다(ports/notifier.py 참고). 여기서 검증하려면 그 상태를 이 프로세스가
어딘가에서 읽어와야 하는데, 그 상태를 만든 프로세스(worker)와 이 프로세스(webhook 서버/
리스너)가 갈라져 있어서 공유할 방법이 없다 — 실제로 어댑터 메모리에 뒀다가 라이브
스모크테스트에서 그렇게 터졌다. signal 은 그냥 nonce 를 실어 보내고, 판단은 워크플로우가 한다.

벤더 SDK(`python-telegram-bot`)를 참조하지 않는다 — 봇 토큰/전송 방식을 아는 곳은
`adapters/notifier/telegram.py` 하나뿐이어야 한다 (§11.3, §11.6). 여기서는 raw dict(Bot API
JSON 스키마 그대로)만 쓴다.

콜백 데이터 형식은 `TelegramNotifier.request_decision` 이 만든다:
`"{a|r}:{application_id}:{nonce}"`.
"""

from dataclasses import dataclass
from typing import Any

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import ApproveSignal, NotifyEvent, RejectSignal
from auto_apply.workflows.application import ApplicationWorkflow

_ACTIONS = {"a": "승인", "r": "거절"}


class MalformedCallback(ValueError):
    """callback_data 가 "{a|r}:{application_id}:{nonce}" 형식이 아니다."""


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    handled: bool
    reason: str = ""


def _parse(data: str) -> tuple[str, str, str]:
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] not in _ACTIONS:
        raise MalformedCallback(data)
    action, application_id, nonce = parts
    return action, application_id, nonce


async def handle_callback_query(
    callback: dict[str, Any], c: Container, client: Client
) -> CallbackOutcome:
    """`callback_query` 하나(raw dict)를 처리한다. 웹훅 라우트와 리스너가 그대로 호출한다.

    signal 은 항상 보낸다 — 오래된/재전달된 콜백이면 워크플로우의 nonce 검증이 조용히
    무시한다(§6). 그래서 여기 반환값 `handled` 는 "signal 을 보냈다"는 뜻이지 "워크플로우가
    그걸 받아들였다"는 뜻은 아니다 — 그건 signal 이 fire-and-forget 이라 이 프로세스가
    알 방법이 없다.
    """
    from_id = (callback.get("from") or {}).get("id")
    if from_id not in c.settings.allowed_chat_ids:
        # 허용되지 않은 사용자 — 이 봇은 실제 제출 권한을 가진 콘솔이다 (§6)
        return CallbackOutcome(handled=False, reason="chat not allowed")

    action, application_id, nonce = _parse(callback.get("data", ""))

    wf_id = f"application-{application_id}"
    decided_by = str(from_id)
    try:
        handle = client.get_workflow_handle(wf_id)
        if action == "a":
            await handle.signal(
                ApplicationWorkflow.approve, ApproveSignal(decided_by=decided_by, nonce=nonce)
            )
        else:
            await handle.signal(
                ApplicationWorkflow.reject, RejectSignal(decided_by=decided_by, nonce=nonce)
            )
    except RPCError as e:
        return CallbackOutcome(handled=False, reason=f"workflow not found: {e.message}")

    await c.notifier.notify(
        NotifyEvent(
            kind="DECISION_RECORDED",
            application_id=application_id,
            message=f"{_ACTIONS[action]} 처리됐습니다.",
        )
    )
    return CallbackOutcome(handled=True)
