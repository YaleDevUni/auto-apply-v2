from anthropic import AsyncAnthropic
from anthropic.types import TextBlockParam
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import LLMSchemaViolation

_TOOL_NAME = "emit"


def _content_blocks(prompt: str, cache_key: str | None) -> list[TextBlockParam]:
    """`cache_key`가 있으면 이 블록에 `cache_control`을 붙인다.

    Anthropic 캐시는 키가 아니라 콘텐츠 프리픽스 해시로 매칭된다 — `cache_key`는 "이 호출은
    같은 프리픽스로 반복될 가능성이 있다"는 신호일 뿐이다(예: `SimpleResumeGenerator`의
    재프롬프트 루프는 원본 프롬프트 전체를 그대로 접두어로 두고 뒤에 오류 메시지만 붙인다 —
    같은 cache_key 로 여러 번 부르면 그 접두어가 캐시로 읽힌다). 신호가 없는 1회성 호출에
    캐시 브레이크포인트를 붙이면 쓰기 비용만 늘 뿐이라 붙이지 않는다.
    """
    block: TextBlockParam = {"type": "text", "text": prompt}
    if cache_key is not None:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


class AnthropicLLM:
    """`ports/llm.py`의 M0 구현 (§11.2). `RecordedLLM`/`StubLLM`이 오프라인 대역이다."""

    def __init__(self, api_key: str, *, model: str = "claude-sonnet-5") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self, prompt: str, *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": _content_blocks(prompt, cache_key)}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> T:
        # tool_choice 로 강제한다 — free-form 텍스트를 파싱해서 스키마에 맞추려 하면 실패
        # 모드가 늘어난다. 스키마 위반은 ValidationError 만 감싼다 — RateLimitError 같은
        # API 레벨 에러까지 여기서 삼키면 §5 의 "일시적 → 재시도" 분류가 깨진다.
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "구조화된 결과를 반환한다.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": _content_blocks(prompt, cache_key)}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                try:
                    return schema.model_validate(block.input)
                except ValidationError as e:
                    raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e
        raise LLMSchemaViolation(f"{schema.__name__}: tool_use 블록이 없다")
