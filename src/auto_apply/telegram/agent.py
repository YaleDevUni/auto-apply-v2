"""자유 텍스트 채팅 → 도구 자동 선택 (ReAct 루프).

`telegram/bridge.py`의 `handle_message`가 REVISE/가이드 patch ForceReply 태그에 매칭되지 않는
자유 텍스트를 여기로 넘긴다. LLM은 매 턴 구조화 출력(`AgentStep`, ai/schemas.py)으로 "도구를
부를지 답할지"만 고르고, 도구 카탈로그 프롬프트 조립은 `domain/chat_agent.py`(포트 무의존)가,
실제 실행(읽기/버튼 재전송)은 이 파일의 `TOOLS` 레지스트리가 한다 — CLAUDE.md의 "AI는 생성만,
판정·조합은 코드" 철학의 연장.

행동성 도구(`resend_pending_decision`)도 워크플로우를 직접 mutate 하지 않는다 — 기존 승인
버튼의 nonce(`ApplicationWorkflow.pending_decision` query)를 그대로 실어 원래 버튼과 동일하게
동작하는 메시지를 다시 보낼 뿐이다. 실제 승인/거절/제출은 여전히 사람이 그 버튼을 누르는
순간에만 일어난다 — CLAUDE.md 절대규칙 4("되돌릴 수 없는 행위는 사람 승인 뒤에서만")를
자연어 오인식 경로로 우회하지 않기 위한 설계다.

도구 하나가 죽어도(존재하지 않는 application_id, 워크플로우 조회 실패 등) 대화 전체가 죽지
않는다 — 예외를 관찰 결과 문자열로 되돌려 모델이 다음 턴에 스스로 정정하게 한다. LLM 호출
자체가 실패(스키마 위반/quota 등)하면 루프를 그 자리에서 접고 사과 메시지로 마무리한다 —
이 핸들러를 부르는 두 진입점(webhook 라우트/롱폴링 리스너) 모두 이 함수가 예외를 던지지
않는다는 전제로 짜여 있다(webhook 은 다른 예외를 잡지 않고 그대로 500을 낸다).
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

import structlog
from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.ai.schemas import AgentStep
from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.domain.chat_agent import MAX_STEPS, ToolCatalogEntry, build_prompt, catalog_prefix
from auto_apply.workflows.application import ApplicationWorkflow

log = structlog.get_logger(__name__)

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]

_MIN_LIMIT = 1
_MAX_LIMIT = 20
_DEFAULT_LIMIT = 5


@runtime_checkable
class _ResendableNotifier(Protocol):
    """`resend_pending_decision` 도구가 쓰는 구조적 계약 — telegram/bridge.py 의

    `_RevisableNotifier`와 같은 이유로 구체 타입(`TelegramNotifier`)을 이름으로 가리키지 않는다.
    """

    async def resend_decision(self, application_id: str, nonce: str) -> None: ...


def _parse_limit(raw: str) -> int:
    try:
        limit = int(raw) if raw else _DEFAULT_LIMIT
    except ValueError:
        limit = _DEFAULT_LIMIT
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


async def _list_applications(args: dict[str, str], c: Container, _client: Client) -> str:
    limit = _parse_limit(args.get("limit", ""))
    async with c.uow() as uow:
        summaries = await uow.applications.list_recent(limit=limit)
    if not summaries:
        return "지원 건이 없습니다."
    lines = [
        f"{s.application_id}: {s.state}" + (f" ({s.reason})" if s.reason else "") for s in summaries
    ]
    return "\n".join(lines)


async def _get_application(args: dict[str, str], c: Container, _client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    async with c.uow() as uow:
        history = await uow.applications.history(application_id)
    if not history:
        return f"{application_id}: 기록이 없습니다."
    latest = history[-1]
    detail = f"{application_id}: {latest.state}"
    if latest.reason:
        detail += f" ({latest.reason})"
    if latest.scheduled_at:
        detail += f", 예약: {latest.scheduled_at.isoformat()}"
    if latest.submitted_at:
        detail += f", 제출: {latest.submitted_at.isoformat()}"
    return detail


async def _list_recipe_versions(args: dict[str, str], c: Container, _client: Client) -> str:
    platform = args.get("platform", "").strip()
    if not platform:
        return "platform이 필요합니다."
    versions = await c.recipes.versions(platform)
    if not versions:
        return f"{platform}: 등록된 recipe 버전이 없습니다."
    return "\n".join(f"v{r.version}: {r.status}" for r in versions)


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
    assert isinstance(c.notifier, _ResendableNotifier)
    await c.notifier.resend_decision(application_id, view.nonce)
    return f"{application_id}: 승인 버튼을 다시 보냈습니다."


# name -> (description, arg 이름들, handler). 늘어날 걸 전제로 한 레지스트리 — 새 도구는
# 여기 항목 하나 추가로 끝난다.
TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "list_applications": (
        "최근 지원 건 목록(상태 포함)을 최신순으로 본다",
        ("limit",),
        _list_applications,
    ),
    "get_application": (
        "특정 지원 건 하나의 최신 상태를 조회한다",
        ("application_id",),
        _get_application,
    ),
    "list_recipe_versions": (
        "특정 플랫폼의 recipe 버전 목록과 각 상태(draft/candidate/active/deprecated)를 본다",
        ("platform",),
        _list_recipe_versions,
    ),
    "resend_pending_decision": (
        "승인 대기 중인 지원 건의 승인/거절/수정요청 버튼을 다시 보낸다"
        " (직접 승인/거절하지 않는다 — 사람이 버튼을 눌러야 한다)",
        ("application_id",),
        _resend_pending_decision,
    ),
}


def _catalog() -> list[ToolCatalogEntry]:
    return [ToolCatalogEntry(name, desc, args) for name, (desc, args, _) in TOOLS.items()]


async def _run_tool(name: str, args: dict[str, str], c: Container, client: Client) -> str:
    entry = TOOLS.get(name)
    if entry is None:
        return f"알 수 없는 도구입니다: {name}. 사용 가능한 도구: {', '.join(TOOLS)}"
    _, _, handler = entry
    try:
        return await handler(args, c, client)
    except Exception as e:  # 도구 하나가 죽어도 대화 전체는 안 죽는다 — 관찰 결과로 되돌린다
        log.warning("telegram.chat_agent.tool_failed", tool=name, error=str(e))
        return f"{name} 실행 중 오류가 발생했습니다: {e}"


async def handle_chat(text: str, c: Container, client: Client) -> None:
    """자유 텍스트 한 턴을 MAX_STEPS까지 도구 호출로 처리하고 최종 답을 notifier 로 보낸다.

    이 함수는 예외를 던지지 않는다 — LLM 호출 자체가 실패해도(스키마 위반/quota 등) 사과
    메시지를 보내고 조용히 끝난다(webhook 라우트가 500 을 내지 않도록).
    """
    prefix = catalog_prefix(_catalog())
    transcript: list[tuple[AgentStep, str]] = []
    try:
        for _ in range(MAX_STEPS):
            prompt = build_prompt(text, transcript)
            step = await c.llm.structured(prompt, AgentStep, cache_prefix=prefix)
            if step.action == "respond":
                await c.notifier.notify(NotifyEvent(kind="CHAT", message=step.response))
                return
            observation = await _run_tool(step.tool, step.tool_args, c, client)
            transcript.append((step, observation))
        await c.notifier.notify(
            NotifyEvent(
                kind="CHAT", message="죄송해요, 요청을 다 처리하지 못했어요. 다시 말씀해주세요."
            )
        )
    except Exception as e:  # 이 함수는 예외를 던지지 않는다 — 모듈 docstring 참고
        log.warning("telegram.chat_agent.turn_failed", error=str(e))
        await c.notifier.notify(
            NotifyEvent(
                kind="CHAT", message="지금 요청을 이해하지 못했어요. 잠시 후 다시 시도해주세요."
            )
        )
