from contextlib import AbstractAsyncContextManager, nullcontext

from anthropic import AsyncAnthropic
from anthropic.types import TextBlockParam
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import LLMSchemaViolation

_TOOL_NAME = "emit"


def _content_blocks(prompt: str, cache_prefix: str) -> list[TextBlockParam]:
    """`cache_prefix`가 있으면 별도 블록으로 분리해 그 경계에 `cache_control`을 붙인다.

    Anthropic 캐시는 "브레이크포인트가 찍힌 블록까지의 전체 콘텐츠"를 하나의 단위로 매칭한다
    — 안정적인 내용과 매번 바뀌는 내용을 같은 블록에 이어붙이면(순서를 어떻게 두든) 한 글자만
    달라져도 그 블록 전체가 캐시 미스가 된다([[claude-cli-prompt-cache-redesign]] 실측). 그래서
    `cache_prefix`(반복되는 부분)를 별도 블록으로 떼어 거기에만 브레이크포인트를 찍고,
    `prompt`(매번 달라지는 부분)는 브레이크포인트 없는 다음 블록에 둔다. `cache_prefix`가
    없는 1회성 호출에 브레이크포인트를 붙이면 쓰기 비용만 늘 뿐이라 붙이지 않는다.
    """
    blocks: list[TextBlockParam] = []
    if cache_prefix:
        blocks.append(
            {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}}
        )
    if prompt or not blocks:
        blocks.append({"type": "text", "text": prompt})
    return blocks


class AnthropicLLM:
    """`ports/llm.py`의 M0 구현 (§11.2). `RecordedLLM`/`StubLLM`이 오프라인 대역이다."""

    def __init__(self, api_key: str, *, model: str = "claude-sonnet-5") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = "") -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": _content_blocks(prompt, cache_prefix)}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
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
            messages=[{"role": "user", "content": _content_blocks(prompt, cache_prefix)}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                try:
                    return schema.model_validate(block.input)
                except ValidationError as e:
                    raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e
        raise LLMSchemaViolation(f"{schema.__name__}: tool_use 블록이 없다")

    def turn(self) -> AbstractAsyncContextManager["AnthropicLLM"]:
        # API 호출 자체엔 프로세스 기동 비용이 없다 — 재사용할 자원이 없어 self 를 그대로
        # 감싼 no-op (ports/llm.py 계약 참고).
        return nullcontext(self)
