"""자유 텍스트 채팅 → 도구 자동 선택 (ReAct 루프).

`telegram/bridge.py`의 `handle_message`가 REVISE/가이드 patch ForceReply 태그에 매칭되지 않는
자유 텍스트를 여기로 넘긴다. LLM은 매 턴 구조화 출력(`AgentStep`, ai/schemas.py)으로 "도구를
부를지 답할지"만 고르고, 도구 카탈로그 프롬프트 조립은 `domain/chat_agent.py`(포트 무의존)가,
실제 실행(읽기/버튼 재전송/워크플로우 시작)은 `telegram/_agent_tools.py`의 `TOOLS` 레지스트리가
한다 — CLAUDE.md의 "AI는 생성만, 판정·조합은 코드" 철학의 연장. 이 파일 자체는 그 루프
오케스트레이션(몇 턴까지 돌릴지, LLM 호출/도구 호출을 어떻게 이어붙일지, 실패를 어떻게 삼킬지)
만 맡는다 — 도구 목록이 늘어날수록 "루프"와 "도구 구현"이 한 파일에서 뒤섞이는 걸 피하려는
분리다(§ CLAUDE.md "한 파일 = 한 책임").

도구 하나가 죽어도(존재하지 않는 application_id, 워크플로우 조회 실패 등) 대화 전체가 죽지
않는다 — 예외를 관찰 결과 문자열로 되돌려 모델이 다음 턴에 스스로 정정하게 한다. LLM 호출
자체가 실패(스키마 위반/quota 등)하면 루프를 그 자리에서 접고 사과 메시지로 마무리한다 —
이 핸들러를 부르는 두 진입점(webhook 라우트/롱폴링 리스너) 모두 이 함수가 예외를 던지지
않는다는 전제로 짜여 있다(webhook 은 다른 예외를 잡지 않고 그대로 500을 낸다).
"""

import structlog
from temporalio.client import Client

from auto_apply.ai.schemas import AgentStep
from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.domain.chat_agent import MAX_STEPS, build_prompt, catalog_prefix
from auto_apply.telegram._agent_tools import SINGLE_SHOT_TOOLS, TOOLS, catalog

log = structlog.get_logger(__name__)


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
    prefix = catalog_prefix(catalog())
    transcript: list[tuple[AgentStep, str]] = []
    # 실측(2026-08-21, telegram-chat-agent-loop-duplicate-start-incident): "2건정도"라고
    # 했는데도 모델이 성공 관찰 결과를 보고 respond로 안 끝내고 같은 도구를 계속 다시 불러
    # start_applications가 한 턴에 여러 번(=요청보다 훨씬 많이) 실제 실행된 적이 있다.
    # 프롬프트로 "한 번만 불러라"를 타이르는 대신, 이미 부른 (도구, 인자) 완전 일치 호출과
    # SINGLE_SHOT_TOOLS(인자가 달라도 한 턴 1회만)는 코드로 재실행을 막는다.
    called_signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    called_tool_names: set[str] = set()
    try:
        for _ in range(MAX_STEPS):
            prompt = build_prompt(text, transcript)
            # 이력서 생성용 c.llm 이 아니라 c.chat_llm — 도구 선택/응답 판단은 훨씬 가벼운
            # 분류 작업이라 더 싼 모델(cfg.telegram_agent_model)을 쓴다(bootstrap.py 참고).
            step = await c.chat_llm.structured(prompt, AgentStep, cache_prefix=prefix)
            if step.action == "respond":
                await c.notifier.notify(NotifyEvent(kind="CHAT", message=step.response))
                return
            signature = (step.tool, tuple(sorted(step.tool_args.items())))
            if signature in called_signatures:
                observation = (
                    f"{step.tool}({step.tool_args})는 이번 턴에 이미 같은 인자로 호출했습니다"
                    " — 다시 부르지 말고 위 결과로 답하세요."
                )
            elif step.tool in SINGLE_SHOT_TOOLS and step.tool in called_tool_names:
                observation = (
                    f"{step.tool}는 이번 턴에 이미 실행했습니다 — 추가로 부르지 말고 위 결과를"
                    " 바탕으로 사용자에게 바로 답하세요."
                )
            else:
                observation = await _run_tool(step.tool, step.tool_args, c, client)
                called_signatures.add(signature)
                called_tool_names.add(step.tool)
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
