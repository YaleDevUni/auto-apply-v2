"""승인 콜백/피드백 메시지 → signal 변환. 웹훅(`api/routers/telegram.py`)과 롱폴링

(`telegram/listener.py`)이 같은 로직을 공유한다 (ARCHITECTURE.md §8 이 계획한 telegram/
패키지의 자리). 인바운드 경로가 둘이어도 "콜백을 어떻게 처리하는가"는 하나여야 어긋나지 않는다.

nonce 검증은 여기서 하지 않는다 — `ApplicationWorkflow` 의 signal 핸들러가 발급된 nonce 를
직접 들고 검증한다(ports/notifier.py 참고). 여기서 검증하려면 그 상태를 이 프로세스가
어딘가에서 읽어와야 하는데, 그 상태를 만든 프로세스(worker)와 이 프로세스(webhook 서버/
리스너)가 갈라져 있어서 공유할 방법이 없다 — 실제로 어댑터 메모리에 뒀다가 라이브
스모크테스트에서 그렇게 터졌다. signal 은 그냥 nonce 를 실어 보내고, 판단은 워크플로우가 한다.

벤더 SDK(`python-telegram-bot`)를 참조하지 않는다 — 봇 토큰/전송 방식을 아는 곳은
`adapters/notifier/telegram.py` 하나뿐이어야 한다 (§11.3, §11.6). 여기서는 raw dict(Bot API
JSON 스키마 그대로)만 쓴다. scope 선택/ForceReply 프롬프트를 보낼 때도 `TelegramNotifier`를
이름으로 가리키지 않고, 구조적 Protocol(`_RevisableNotifier`)로 "그 메서드가 있는 notifier"
인지만 본다 — 테스트가 nonce 관찰용으로 감싸는 대역도 같은 메서드만 있으면 통과해야 해서다.

콜백 데이터 형식(모두 nonce 를 마지막에 둔다 — 값 안에 콜론이 있어도 안전하게 split 되도록):
- `"{a|r|v}:{application_id}:{nonce}"` — 승인/거절/수정요청 시작 (TelegramNotifier._keyboard)
- `"vs:{application_id}:{specific|general}:{nonce}"` — 수정요청 범위 선택
- `"vc:{application_id}:{nonce}"` — 수정요청 취소 (scope 선택/피드백 입력 화면 모두에서 쓴다,
  signal 없음 — 원래 승인/거절/수정요청 버튼의 nonce 가 아직 안 쓰였으므로 안내만 보낸다)
- `"{ga|gr|gv}:{application_id}:{nonce}"` — 가이드 patch 승인/거절/코멘트 시작
- `"gc:{application_id}:{nonce}"` — 가이드 patch 코멘트 취소 (`vc`와 같은 이유, 안내 문구만 다르다)
- `"{ca|cr}:{application_id}:{nonce}"` — SUPERVISED 페이지 경계 체크포인트 승인/거절
  (§ supervised-checkpoint-design). 워크플로우가 아니라 activity(`CheckpointWaiter`)가
  기다리는 대상이라 signal 경로를 안 탄다 — `c.checkpoint_store.record_decision`을 직접
  호출한다(nonce 검증과 같은 이유로 이 어댑터/서버 메모리에 상태를 못 둔다).
- `"{pa|pr}:{platform}-{form_hash}:{nonce}"` — recipe 승격 승인/보류 (§2.4). 다른 액션과 달리
  `application_id` 자리가 실제 지원 건이 아니라 `AutomationRepairWorkflow`의 정체성
  (`platform`/`form_hash`)을 담는다 — `wf_id = f"repair-{platform}-{form_hash}"`를 그대로
  복원할 수 있게(workflows/repair.py `_await_promotion`).

REVISE 자유 텍스트 피드백은 콜백이 아니라 `message`(ForceReply 답장)로 온다 —
`[revise:{application_id}:{nonce}:{scope}]` 태그를 프롬프트 메시지 본문에 실어 보내고,
답장의 `reply_to_message.text`에서 그 태그를 파싱해 복원한다(TelegramNotifier.send_feedback_prompt
참고) — 프로세스 경계를 넘나드는 상태를 안 들고도 어느 요청에 대한 답인지 알 수 있다. 가이드
patch 코멘트도 같은 방식으로 `[guiderevise:{application_id}:{nonce}]` 태그를 쓴다(scope 가
없다 — 가이드 patch 자체가 이미 GENERAL 범위다).
"""

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import (
    ApproveSignal,
    GuidePatchDecisionSignal,
    GuidePatchReviseSignal,
    NotifyEvent,
    RejectSignal,
    ReviseSignal,
)
from auto_apply.domain.enums import RevisionScope
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.repair import AutomationRepairWorkflow

_ACTIONS = {
    "a": "승인",
    "r": "거절",
    "ga": "가이드 반영",
    "gr": "가이드 무시",
    "pa": "recipe 승격",
    "pr": "recipe 승격 보류",
}
# pa/pr(recipe 승격, §2.4)은 `application-{id}` 가 아니라 `repair-{id}` 워크플로우를 겨눈다 —
# AutomationRepairWorkflow 의 승인 요청은 application_id 자리에 "{platform}-{form_hash}"를
# 담아 보낸다(DecisionRequest.repair_promotion, workflows/repair.py 참고).
_REPAIR_ACTIONS = frozenset({"pa", "pr"})
_REVISE_TAG_RE = re.compile(r"\[revise:([^:\s]+):([^:\s]+):(specific|general)\]")
_GUIDE_REVISE_TAG_RE = re.compile(r"\[guiderevise:([^:\s]+):([^:\s]+)\]")


class MalformedCallback(ValueError):
    """callback_data 가 알려진 형식이 아니다."""


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    handled: bool
    reason: str = ""


def _parse(
    data: str, valid: frozenset[str] = frozenset({*_ACTIONS, "v", "gv", "vc", "gc", "ca", "cr"})
) -> tuple[str, str, str]:
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] not in valid:
        raise MalformedCallback(data)
    action, application_id, nonce = parts
    return action, application_id, nonce


def _parse_scope_choice(data: str) -> tuple[str, str, str]:
    """`"vs:{application_id}:{specific|general}:{nonce}"` — nonce 를 마지막에 둬서

    안전하게 4번째 조각까지 split 한다.
    """
    parts = data.split(":", 3)
    if len(parts) != 4 or parts[0] != "vs" or parts[2] not in ("specific", "general"):
        raise MalformedCallback(data)
    _, application_id, scope, nonce = parts
    return application_id, scope, nonce


@runtime_checkable
class _RevisableNotifier(Protocol):
    """REVISE UI(scope 선택/ForceReply) 를 보낼 수 있는 notifier 의 구조적 계약.

    `TelegramNotifier`(adapters/notifier/telegram.py)를 이름으로 가리키지 않는다 — 테스트가
    nonce 관찰용으로 감싸는 대역(`_NonceSpy`)도 같은 메서드만 있으면 통과해야 하기 때문에
    `@runtime_checkable`로 구조적으로 판정한다.
    """

    async def send_scope_picker(self, application_id: str, nonce: str) -> None: ...

    async def send_feedback_prompt(
        self, application_id: str, nonce: str, scope: RevisionScope
    ) -> None: ...

    async def send_guide_feedback_prompt(self, application_id: str, nonce: str) -> None: ...

    async def answer_callback_query(self, callback_query_id: str) -> None: ...


def _telegram(c: Container) -> _RevisableNotifier:
    # 이 함수는 c.settings.notifier == "telegram" 일 때만 호출된다(라우트/리스너가 먼저 걸러준다).
    assert isinstance(c.notifier, _RevisableNotifier)
    return c.notifier


async def handle_callback_query(
    callback: dict[str, Any], c: Container, client: Client
) -> CallbackOutcome:
    """`callback_query` 하나(raw dict)를 처리한다. 웹훅 라우트와 리스너가 그대로 호출한다.

    signal 은 항상 보낸다 — 오래된/재전달된 콜백이면 워크플로우의 nonce 검증이 조용히
    무시한다(§6). 그래서 여기 반환값 `handled` 는 "signal 을 보냈다"는 뜻이지 "워크플로우가
    그걸 받아들였다"는 뜻은 아니다 — 그건 signal 이 fire-and-forget 이라 이 프로세스가
    알 방법이 없다. `v`/`vs`/`gv`는 signal 이 아니라 다음 안내 메시지를 보낼 뿐이다.
    `vc`/`gc`(취소)는 그마저도 없다 — 원래 버튼의 nonce 가 아직 안 쓰인 채로 남아있으니
    사용자에게 취소됐다고만 알려주면 된다.

    무엇보다 먼저 `answerCallbackQuery`를 호출한다 — 안 그러면 버튼을 눌렀을 때 뜨는 "불러오는
    중" 스피너가 이후 어떤 처리를 하든 안 꺼진다(라이브 스모크테스트로 실측, 허용 안 된
    chat 이어도 눌러본 사람 입장에선 꺼줘야 한다).
    """
    await _telegram(c).answer_callback_query(callback.get("id", ""))

    from_id = (callback.get("from") or {}).get("id")
    if from_id not in c.settings.allowed_chat_ids:
        # 허용되지 않은 사용자 — 이 봇은 실제 제출 권한을 가진 콘솔이다 (§6)
        return CallbackOutcome(handled=False, reason="chat not allowed")

    data = callback.get("data", "")
    if data.startswith("vs:"):
        application_id, scope, nonce = _parse_scope_choice(data)
        await _telegram(c).send_feedback_prompt(application_id, nonce, RevisionScope(scope))
        return CallbackOutcome(handled=True)

    action, application_id, nonce = _parse(data)
    if action == "v":
        await _telegram(c).send_scope_picker(application_id, nonce)
        return CallbackOutcome(handled=True)
    if action == "gv":
        await _telegram(c).send_guide_feedback_prompt(application_id, nonce)
        return CallbackOutcome(handled=True)
    if action == "vc":
        await c.notifier.notify(
            NotifyEvent(
                kind="DECISION_RECORDED",
                application_id=application_id,
                message="수정요청을 취소했습니다. 기존 승인/거절/수정요청 버튼을 사용하세요.",
            )
        )
        return CallbackOutcome(handled=True)
    if action == "gc":
        await c.notifier.notify(
            NotifyEvent(
                kind="DECISION_RECORDED",
                application_id=application_id,
                message="코멘트를 취소했습니다. 기존 반영/무시/코멘트 버튼을 사용하세요.",
            )
        )
        return CallbackOutcome(handled=True)
    if action in ("ca", "cr"):
        # 체크포인트는 워크플로우가 아니라 activity(CheckpointWaiter)가 기다린다 — signal 이
        # 아니라 CheckpointStore 에 직접 기록한다(§ supervised-checkpoint-design).
        await c.checkpoint_store.record_decision(nonce, approved=action == "ca")
        await c.notifier.notify(
            NotifyEvent(
                kind="DECISION_RECORDED",
                application_id=application_id,
                message="체크포인트를 승인했습니다."
                if action == "ca"
                else "체크포인트를 거절했습니다.",
            )
        )
        return CallbackOutcome(handled=True)

    wf_id = (
        f"repair-{application_id}" if action in _REPAIR_ACTIONS else f"application-{application_id}"
    )
    decided_by = str(from_id)
    try:
        handle = client.get_workflow_handle(wf_id)
        match action:
            case "a":
                await handle.signal(
                    ApplicationWorkflow.approve, ApproveSignal(decided_by=decided_by, nonce=nonce)
                )
            case "r":
                await handle.signal(
                    ApplicationWorkflow.reject, RejectSignal(decided_by=decided_by, nonce=nonce)
                )
            case "ga":
                await handle.signal(
                    ApplicationWorkflow.approve_guide_patch,
                    GuidePatchDecisionSignal(decided_by=decided_by, nonce=nonce),
                )
            case "gr":
                await handle.signal(
                    ApplicationWorkflow.reject_guide_patch,
                    GuidePatchDecisionSignal(decided_by=decided_by, nonce=nonce),
                )
            case "pa":
                await handle.signal(
                    AutomationRepairWorkflow.approve,
                    ApproveSignal(decided_by=decided_by, nonce=nonce),
                )
            case _:  # "pr"
                await handle.signal(
                    AutomationRepairWorkflow.reject,
                    RejectSignal(decided_by=decided_by, nonce=nonce),
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


async def handle_message(message: dict[str, Any], c: Container, client: Client) -> CallbackOutcome:
    """REVISE/가이드 patch 코멘트 ForceReply 답장(`message`, raw dict)을 처리한다.

    `[revise:...]`/`[guiderevise:...]` 태그가 안 붙은 답장(태그 있는 프롬프트에 대한 답이 아닌
    일반 대화)은 조용히 무시한다 — 이 봇이 다루는 유일한 자유 텍스트 인바운드가 이 태그
    답장들뿐이다.
    """
    from_id = (message.get("from") or {}).get("id")
    if from_id not in c.settings.allowed_chat_ids:
        return CallbackOutcome(handled=False, reason="chat not allowed")

    reply_to = message.get("reply_to_message") or {}
    reply_text = reply_to.get("text", "")
    feedback = (message.get("text") or "").strip()

    guide_match = _GUIDE_REVISE_TAG_RE.search(reply_text)
    if guide_match is not None:
        if not feedback:
            return CallbackOutcome(handled=False, reason="empty feedback")
        application_id, nonce = guide_match.groups()
        wf_id = f"application-{application_id}"
        try:
            handle = client.get_workflow_handle(wf_id)
            await handle.signal(
                ApplicationWorkflow.revise_guide_patch,
                GuidePatchReviseSignal(feedback=feedback, decided_by=str(from_id), nonce=nonce),
            )
        except RPCError as e:
            return CallbackOutcome(handled=False, reason=f"workflow not found: {e.message}")
        await c.notifier.notify(
            NotifyEvent(
                kind="DECISION_RECORDED",
                application_id=application_id,
                message="가이드 patch 코멘트가 접수됐습니다.",
            )
        )
        return CallbackOutcome(handled=True)

    match = _REVISE_TAG_RE.search(reply_text)
    if match is None:
        return CallbackOutcome(handled=False, reason="not a revise reply")
    if not feedback:
        return CallbackOutcome(handled=False, reason="empty feedback")

    application_id, nonce, scope = match.groups()
    wf_id = f"application-{application_id}"
    try:
        handle = client.get_workflow_handle(wf_id)
        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(
                feedback=feedback,
                scope=RevisionScope(scope),
                decided_by=str(from_id),
                nonce=nonce,
            ),
        )
    except RPCError as e:
        return CallbackOutcome(handled=False, reason=f"workflow not found: {e.message}")

    await c.notifier.notify(
        NotifyEvent(
            kind="DECISION_RECORDED",
            application_id=application_id,
            message="수정요청이 접수됐습니다.",
        )
    )
    return CallbackOutcome(handled=True)
