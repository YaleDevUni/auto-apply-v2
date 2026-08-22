"""자유 텍스트 채팅 → 도구 선택 프롬프트 조립 — 순수 함수, 포트 무의존.

`telegram/agent.py`가 실제 도구 실행(uow 읽기, Temporal query, notifier 호출)과 루프
오케스트레이션을 맡고, 여기는 그 도구 카탈로그를 LLM 프롬프트 문자열로 바꾸는 것만 한다 —
"AI는 생성만, 판정·조합은 코드" 철학의 연장이자, 포트를 몰라야 어댑터 없이 테스트할 수 있어서다
(§11.1 원칙 — domain 은 프레임워크/어댑터 무의존).
"""

from dataclasses import dataclass

from auto_apply.ai.schemas import AgentStep

# 4였는데 "스케줄 시각 바꾸고 켜줘"처럼 도구 2개가 필요한 요청이 매번 소진됐다(실측
# 2026-08-22): 모델이 성공한 도구를 한 번씩 더 부르는 습성이 있어 도구 N개에 2N+1 스텝이
# 든다. 중복 호출은 telegram/agent.py 가 코드로 막아 실제 실행 횟수는 안 늘어나므로
# (도구·인자 조합당 1회, SINGLE_SHOT_TOOLS 는 턴당 1회) 상한만 넉넉히 올린다.
MAX_STEPS = 8

_SYSTEM = (
    "당신은 auto-apply 콘솔의 텔레그램 채팅 에이전트입니다. 사용자의 자연어 요청을 아래 도구로만"
    " 처리하세요. 모르면 지어내지 말고 도구로 확인하거나 모른다고 답하세요. 반드시 JSON 하나로만"
    ' 답합니다 — 도구를 부르려면 action="call_tool", 최종 답을 할 준비가 되면 action="respond".'
    " 같은 도구를 같은 인자로 두 번 부르지 마세요 — 결과가 이미 나온 도구는 다시 부르지 말고,"
    " 아직 안 한 일이 남았을 때만 다음 도구를 부르세요. 요청한 일을 다 했으면 곧바로"
    ' action="respond" 로 끝내세요.'
)


@dataclass(frozen=True, slots=True)
class ToolCatalogEntry:
    """LLM에게 보여줄 도구 설명 하나. 핸들러(콜러블)는 안 담는다 — 그건 telegram/agent.py 의

    레지스트리에만 있고, 여기는 포트를 몰라야 한다.
    """

    name: str
    description: str
    args: tuple[str, ...] = ()


def catalog_prefix(catalog: list[ToolCatalogEntry]) -> str:
    """대화 내내 안 바뀌는 부분 — `LLMClient.structured(..., cache_prefix=...)`에 그대로

    물린다 (턴마다 캐시가 재사용된다, [[claude-cli-prompt-cache-redesign]] 패턴 재사용).
    """
    lines = [_SYSTEM, "", "사용 가능한 도구:"]
    for tool in catalog:
        args = f"({', '.join(tool.args)})" if tool.args else "()"
        lines.append(f"- {tool.name}{args}: {tool.description}")
    return "\n".join(lines)


def build_prompt(user_text: str, transcript: list[tuple[AgentStep, str]]) -> str:
    """이번 턴의 사용자 원문 + 지금까지 이번 턴에서 부른 (도구 호출, 관찰 결과) 를 이어붙인다.

    `catalog_prefix`는 여기 안 들어간다 — `cache_prefix`로 따로 실려서 캐시 경계가 갈린다.
    """
    lines = [f"사용자: {user_text}"]
    for step, observation in transcript:
        lines.append(f"[도구 호출] {step.tool}({step.tool_args}) -> {observation}")
    if transcript:
        # 모델이 성공 관찰 결과를 보고도 같은 도구를 또 부르는 습성이 있어(MAX_STEPS 주석)
        # 매 턴 끝에 종료 조건을 다시 상기시킨다.
        lines.append(
            '위 결과로 사용자에게 답할 수 있으면 action="respond" 로 끝내세요.'
            " 아직 실행하지 않은 다른 도구가 필요할 때만 도구를 부르세요."
        )
    return "\n".join(lines)


def build_fallback_message(executed: list[tuple[str, str]]) -> str:
    """MAX_STEPS 를 소진했을 때 사용자에게 보낼 메시지.

    실제로 실행된 도구가 있으면 그 관찰 결과를 그대로 보여준다 — 모델이 `respond` 로 못
    끝냈을 뿐 도구는 이미 돌았기 때문에, 사과만 보내면 사용자가 "실패했다"고 오해하고 같은
    요청을 다시 보낸다(실측 2026-08-22, 스케줄을 실제로 켜놓고 실패 메시지를 보냈다).
    """
    if not executed:
        return "죄송해요, 요청을 다 처리하지 못했어요. 다시 말씀해주세요."
    lines = ["요약 정리는 못 했지만, 아래 작업은 실제로 실행됐어요:"]
    lines += [f"- {name}: {observation}" for name, observation in executed]
    return "\n".join(lines)
