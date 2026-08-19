from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import LLMSchemaViolation

_TOOL_NAME = "emit"


class AnthropicLLM:
    """`ports/llm.py`의 M0 구현 (§11.2). `RecordedLLM`/`StubLLM`이 오프라인 대역이다."""

    def __init__(self, api_key: str, *, model: str = "claude-sonnet-5") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, prompt: str, *, max_tokens: int = 2048) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048
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
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                try:
                    return schema.model_validate(block.input)
                except ValidationError as e:
                    raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e
        raise LLMSchemaViolation(f"{schema.__name__}: tool_use 블록이 없다")
