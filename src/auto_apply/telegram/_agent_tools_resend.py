"""승인 버튼 재전송 도구 — `telegram/_agent_tools.py`가 `TOOLS`에 병합한다.

`resend_pending_decision`(application_id 지정)만 있던 걸 `resend_all_pending_decisions`
(전부 일괄)로 확장했다(2026-08-23, 리스너가 죽어 있던 동안 밀린 승인을 재기동 후 한 번에
다시 보고 싶다는 요청 — § 리스너 SIGTERM 직후 세션). 둘 다 워크플로우를 mutate 하지 않는다
— 기존 승인 요청이 들고 있는 nonce(`ApplicationWorkflow.pending_decision` query)를 그대로
실어 원래 버튼과 동일하게 동작하는 메시지를 다시 보낼 뿐이다(CLAUDE.md 절대규칙 4를 자연어
오인식으로 우회하지 않는다는 불변식).

일괄 조회/재전송 로직 자체(`find_pending_decisions`/`resend_all`)는 `pending_decisions.py`
에 있다 — `cli.py`의 `resend-pending` 명령과 이 도구가 같은 코드를 공유한다(그 파일
docstring 참고). `_agent_tools.py`가 이미 200줄을 넘어서(§ CLAUDE.md "한 파일 = 한 책임")
`_agent_tools_schedule.py`/`_agent_tools_collect.py`와 같은 이유로 새 파일로 뺐다.
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.pending_decisions import ResendableNotifier, resend_all
from auto_apply.workflows.application import ApplicationWorkflow

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]


async def _resend_pending_decision(args: dict[str, str], c: Container, client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    try:
        handle = client.get_workflow_handle(f"application-{application_id}")
        view = await handle.query(ApplicationWorkflow.pending_decision)
    except RPCError as e:
        return f"{application_id}: 워크플로우를 찾을 수 없습니다 ({e.message})"
    if not view.has_pending:
        return f"{application_id}: 대기 중인 승인이 없습니다."
    # 이 함수는 c.settings.notifier == "telegram" 일 때만 불린다(bridge.handle_message 가 먼저
    # 걸러준다) — bridge.py 의 `_telegram(c)` 와 같은 불변식.
    assert isinstance(c.notifier, ResendableNotifier)
    await c.notifier.resend_decision(application_id, view.nonce)
    return f"{application_id}: 승인 버튼을 다시 보냈습니다."


async def _resend_all_pending_decisions(_args: dict[str, str], c: Container, client: Client) -> str:
    assert isinstance(c.notifier, ResendableNotifier)  # 위와 같은 불변식
    pending = await resend_all(client, c.notifier)
    if not pending:
        return "대기 중인 승인이 없습니다."
    lines = [f"{len(pending)}건의 승인 버튼을 다시 보냈습니다:"]
    lines += [f"  - {p.application_id}" for p in pending]
    return "\n".join(lines)


RESEND_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "resend_pending_decision": (
        "승인 대기 중인 지원 건 하나의 승인/거절/수정요청 버튼을 다시 보낸다"
        " (직접 승인/거절하지 않는다 — 사람이 버튼을 눌러야 한다)",
        ("application_id",),
        _resend_pending_decision,
    ),
    "resend_all_pending_decisions": (
        "승인 대기 중인 지원 건을 전부 찾아 각각의 승인/거절/수정요청 버튼을 한 번에 다시"
        " 보낸다(직접 승인/거절하지 않는다). application_id를 몰라도 쓸 수 있다 — '대기 중인"
        " 거 다시 보여줘', '승인 기다리는 공고 다시 띄워줘' 같은 요청에 쓴다. 리스너가 잠깐"
        " 꺼져 있던 동안 못 받았을 버튼을 재기동 후 한 번에 복구할 때도 유용하다.",
        (),
        _resend_all_pending_decisions,
    ),
}
