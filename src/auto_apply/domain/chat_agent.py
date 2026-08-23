"""자유 텍스트 채팅 → 도구 선택 프롬프트 조립 — 순수 함수, 포트 무의존.

`telegram/agent.py`가 실제 도구 실행(uow 읽기, Temporal query, notifier 호출)과 루프
오케스트레이션을 맡고, 여기는 그 도구 카탈로그를 LLM 프롬프트 문자열로 바꾸는 것만 한다 —
"AI는 생성만, 판정·조합은 코드" 철학의 연장이자, 포트를 몰라야 어댑터 없이 테스트할 수 있어서다
(§11.1 원칙 — domain 은 프레임워크/어댑터 무의존).
"""

from dataclasses import dataclass
from typing import Literal

from auto_apply.ai.schemas import AgentStep

# 실제로 실행됐는지(done/failed)와 이번 턴에 중복이라 아예 안 돈 것(blocked)을 구분한다 —
# 셋 다 "✅"로 뭉뚱그리면 모델이 실패한 도구 호출도 성공으로 읽고 사용자에게 그렇게 답한다
# (/code-review finding #3: start_applications 가 실패했는데 "지원을 시작했습니다"로 답함).
ToolCallStatus = Literal["done", "failed", "blocked"]
_STATUS_GLYPH: dict[ToolCallStatus, str] = {"done": "✅", "failed": "⚠️", "blocked": "🔁"}

# 실제로 진전을 낸 스텝(도구를 실행했거나 respond 한 스텝)의 상한. 원래 4였는데 "스케줄
# 시각 바꾸고 켜줘"처럼 도구 2개가 필요한 요청이 매번 소진됐다(실측 2026-08-22).
MAX_STEPS = 8
# 중복으로 차단돼 아무 일도 안 한 스텝의 상한 — 무한 루프 방지용 별도 예산이다.
# 모델이 성공한 도구를 한 번씩 더 부르는 습성이 있어(실측 2026-08-22: 도구 2개 요청에
# 중복 시도가 4번) 이걸 MAX_STEPS 와 같이 세면 도구를 3개 이상 부르는 요청이 다시
# 소진된다. 중복 호출은 telegram/agent.py 가 코드로 막아 실제 실행은 (도구·인자 조합당
# 1회, SINGLE_SHOT_TOOLS 는 턴당 1회) 늘어나지 않으므로, 예산을 따로 두는 게 안전하다.
MAX_BLOCKED_STEPS = 8

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


def build_prompt(user_text: str, transcript: list[tuple[AgentStep, str, ToolCallStatus]]) -> str:
    """이번 턴의 사용자 원문 + 지금까지 이번 턴에서 부른 (도구 호출, 관찰 결과, 상태) 를 이어붙인다.

    `catalog_prefix`는 여기 안 들어간다 — `cache_prefix`로 따로 실려서 캐시 경계가 갈린다.

    포맷이 "완료 체크리스트"인 이유: `LLMClient` 에는 멀티턴 tool-use 프리미티브가 없어서
    (`complete`/`structured` 뿐, §9.2) 매 스텝이 무상태 재판단이고, 지난 호출은 모델 자신의
    assistant 턴이 아니라 사용자 메시지 안의 텍스트로만 들어온다. 이걸 `[도구 호출] x -> y`
    같은 로그 한 줄로 적으면 모델이 "내가 이미 했다"로 못 읽고 요청에서 제일 눈에 띄는 도구를
    다시 고른다 — 실측(2026-08-22, claude CLI + haiku, 서로 다른 복합 요청 3건): 로그 형식은
    매번 중복 호출 3~4회, 아래 체크리스트 형식은 3건 모두 중복 0회였다. 중복은 코드가 막아
    실행되진 않지만(telegram/agent.py) 스텝 예산과 LLM 호출비를 태운다.

    상태별로 다른 글리프를 쓴다 — 셋 다 ✅ 로 적으면 모델이 실패한 호출도 성공으로 읽는다
    (finding #3). done(실제 성공)만 ✅, failed(실행됐지만 예외)는 ⚠️, blocked(이번 턴 중복이라
    아예 안 돈 호출)는 🔁 로 구분한다.
    """
    lines = [f"사용자 요청: {user_text}", ""]
    if not transcript:
        lines.append("아직 아무 도구도 실행하지 않았습니다.")
        return "\n".join(lines)
    lines.append("지금까지 이번 턴에서 부른 도구 호출입니다 (다시 부르면 무시됩니다):")
    for i, (step, observation, status) in enumerate(transcript, 1):
        lines.append(f"{i}. {_STATUS_GLYPH[status]} {step.tool}({step.tool_args})")
        lines.append(f"   결과: {observation}")
    lines += [
        "",
        "✅ 는 성공, ⚠️ 는 실패, 🔁 는 중복이라 실행되지 않은 호출입니다. 이미 나온 호출은"
        " 다시 부르지 마세요 — ⚠️/🔁 라고 다른 인자로 다시 시도하지 말고, 실패했다면 실패했다고"
        " 있는 그대로 답하세요. 사용자 요청 중 아직 시도하지 않은 것이 남아 있으면 그 도구만"
        ' 부르고, 남은 게 없으면 action="respond" 로 결과를 정리해 답하세요.',
    ]
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
