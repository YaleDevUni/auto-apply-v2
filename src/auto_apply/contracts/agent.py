"""AgentRuntime 이 주고받는 모양 (§A6).

벤더 SDK 타입이 없다 — 런타임 구현이 자기 형식으로 옮긴다.
"""

from enum import StrEnum

from pydantic import Field

from auto_apply.contracts._base import Frozen


class AgentTool(Frozen):
    """에이전트에게 보이는 도구 하나. `input_schema` 는 JSON Schema(입력 모델에서 만든다)."""

    name: str
    description: str
    input_schema: dict[str, object]


class ToolReply(Frozen):
    """도구 한 번의 결과를 런타임에 돌려주는 모양.

    `content` 는 에이전트에게 그대로 보일 JSON 문자열이다. `done` 이면 run 이 끝났다 —
    런타임은 더 부르지 않고 멈춘다(불러도 거부된다).
    """

    ok: bool
    content: str
    done: bool = False


class AgentLimits(Frozen):
    """run 한 번의 상한. 넘기면 그 run 은 FAILED 로 끝난다(재시도 없음)."""

    max_tool_calls: int = Field(default=200, ge=1)
    # 사람 대기(request_login 등, 기본 600초)를 품을 만큼 길게
    max_seconds: float = Field(default=1800.0, gt=0)


class AgentEnd(StrEnum):
    COMPLETED = "completed"  # 에이전트가 스스로 멈췄거나 done 을 받았다
    TOOL_LIMIT = "tool_limit"
    TIME_LIMIT = "time_limit"


class AgentOutcome(Frozen):
    """런타임이 끝난 모양. 상태 전이는 이것이 아니라 도구 결과로 정한다 — `text` 는 기록용이다."""

    ended: AgentEnd
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    text: str = ""
