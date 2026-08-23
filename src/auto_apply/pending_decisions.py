"""승인 대기 중인 지원 건을 한꺼번에 찾아 승인/거절/수정요청 버튼을 다시 보낸다.

리스너(`telegram/listener.py`)가 죽어 있던 동안에도 워크플로우는 계속 승인을 기다리고
있을 수 있다 — 그 사이 눌린 버튼은 리스너가 못 받았을 뿐, 워크플로우 쪽 nonce는 그대로
살아있다(`ApplicationWorkflow.pending_decision` query). 기존
`resend_pending_decision`(telegram/_agent_tools_resend.py) 도구는 application_id를 미리
알아야 하는데, 리스너 재기동 직후 사람은 애초에 어떤 지원 건이 대기 중인지부터 모른다 — 그
갭을 메운다.

새 port를 만들지 않는다 — `cli.py`/`watchdog.py`와 같은 운영 진입점 계층에서 Temporal
Client를 직접 쓴다(§ CLAUDE.md 운영 진입점 규칙). 대기 여부 판정은 워크플로우 자신의
`pending_decision` query가 유일한 소스라 여기서 다시 추정하지 않는다 — watchdog.py가
FAILED/TERMINATED로 *닫힌* 워크플로우를 찾는 것과 반대로, 이건 아직 RUNNING인 워크플로우
중에서 고른다.

`resend_decision`은 TelegramNotifier 전용이라(Notifier port 표면엔 없음, §11.6 "Telegram
이라는 단어를 모르는 port") 여기서도 구조적 Protocol로만 가리킨다 — `telegram/_agent_tools.py`
의 `_ResendableNotifier`, `telegram/bridge.py`의 `_RevisableNotifier`와 같은 패턴. 호출측이
`c.settings.notifier == "telegram"`을 먼저 걸러야 한다는 불변식도 동일하다.

재전송은 `pending_decision` query가 돌려주는 원래 `DecisionRequest`를 그대로 다시 보낸다
(2026-08-23) — application_id(해시) 한 줄만 보내던 최초 버전은 사람이 뭘 승인하는지 목록만
봐서는 알 수 없다는 지적을 받았다. `TelegramNotifier.resend_decision`이
`request_decision`과 같은 렌더링(모드 배지·공고 링크·PDF 첨부·주의사항)을 재사용한다.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog
from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.contracts.dto import DecisionRequest
from auto_apply.workflows.application import ApplicationWorkflow

log = structlog.get_logger(__name__)

_WORKFLOW_ID_PREFIX = "application-"
# repair-* 등 다른 워크플로우 타입은 WorkflowType 필터로 이미 안 섞이지만, application_id를
# 뽑아내려면 접두어를 알아야 한다(§ cli.py/apply_intake.py의 같은 패턴).
_QUERY = "WorkflowType = 'ApplicationWorkflow' AND ExecutionStatus = 'Running'"


@dataclass(frozen=True)
class PendingDecision:
    application_id: str
    nonce: str
    # pending_decision query 가 돌려준 원래 DecisionRequest — 재전송이 이걸 그대로 다시
    # 렌더링한다. 대기 중이면 워크플로우가 항상 같이 채워주므로 실질적으로 None 이 되는
    # 경우는 없다(방어적으로만 Optional).
    request: DecisionRequest | None = None

    @property
    def label(self) -> str:
        """사람이 읽을 만한 표시명(목록 출력용) — application_id(해시)만 봐선 어떤 공고인지

        알 수 없다는 문제(2026-08-23, 사용자 지적)로 추가했다. request 가 없으면(이론상만
        가능) application_id 로 fallback한다.
        """
        return self.request.title if self.request is not None else self.application_id


@runtime_checkable
class ResendableNotifier(Protocol):
    async def resend_decision(self, request: DecisionRequest, nonce: str) -> None: ...


async def find_pending_decisions(client: Client) -> list[PendingDecision]:
    """실행 중인 `ApplicationWorkflow` 를 전부 훑어 승인 대기 중인 것만 추린다."""
    found: list[PendingDecision] = []
    async for execution in client.list_workflows(_QUERY):
        application_id = execution.id.removeprefix(_WORKFLOW_ID_PREFIX)
        handle = client.get_workflow_handle(execution.id, run_id=execution.run_id)
        try:
            view = await handle.query(ApplicationWorkflow.pending_decision)
        except RPCError as e:
            # 조회 사이 워크플로우가 막 끝났거나 워커가 안 떠서 응답을 못 받는 경우 — 이번엔
            # 건너뛰고, 여전히 대기 중이라면 다음 호출에서 다시 잡힌다.
            log.warning("pending_decisions.query_failed", workflow_id=execution.id, error=e.message)
            continue
        if view.has_pending:
            found.append(
                PendingDecision(
                    application_id=application_id, nonce=view.nonce, request=view.request
                )
            )
    return found


async def resend_all(client: Client, notifier: ResendableNotifier) -> list[PendingDecision]:
    """대기 중인 것들을 찾아 전부 재전송하고, 재전송한 목록을 그대로 돌려준다."""
    pending = await find_pending_decisions(client)
    for p in pending:
        if p.request is not None:
            await notifier.resend_decision(p.request, p.nonce)
    return pending
