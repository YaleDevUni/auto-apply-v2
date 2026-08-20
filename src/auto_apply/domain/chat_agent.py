"""자유 텍스트 채팅 → 도구 선택 프롬프트 조립 — 순수 함수, 포트 무의존.

`telegram/agent.py`가 실제 도구 실행(uow 읽기, Temporal query, notifier 호출)과 루프
오케스트레이션을 맡고, 여기는 그 도구 카탈로그를 LLM 프롬프트 문자열로 바꾸는 것만 한다 —
"AI는 생성만, 판정·조합은 코드" 철학의 연장이자, 포트를 몰라야 어댑터 없이 테스트할 수 있어서다
(§11.1 원칙 — domain 은 프레임워크/어댑터 무의존).
"""

from dataclasses import dataclass

from auto_apply.ai.schemas import AgentStep

MAX_STEPS = 4

_SYSTEM = (
    "당신은 auto-apply 콘솔의 텔레그램 채팅 에이전트입니다. 사용자의 자연어 요청을 아래 도구로만"
    " 처리하세요. 모르면 지어내지 말고 도구로 확인하거나 모른다고 답하세요. 반드시 JSON 하나로만"
    ' 답합니다 — 도구를 부르려면 action="call_tool", 최종 답을 할 준비가 되면 action="respond".'
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
    return "\n".join(lines)
