from contextlib import AbstractAsyncContextManager, nullcontext

from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import LLMSchemaViolation


class StubLLM:
    """네트워크·토큰 없이 파이프라인을 돌리기 위한 어댑터.

    responses 에 등록된 값을 순서대로 반환한다. 등록이 없으면 스키마의 기본값으로
    인스턴스를 만들되, 필수 필드가 있으면 LLMSchemaViolation 을 던져 계약을 지킨다.
    """

    def __init__(
        self, responses: list[str] | None = None, payloads: list[dict[str, object]] | None = None
    ) -> None:
        self._responses = list(responses or [])
        self._payloads = list(payloads or [])

    async def complete(self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = "") -> str:
        if self._responses:
            return self._responses.pop(0)
        return f"[stub completion for {len(prompt)} chars]"

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T:
        payload = self._payloads.pop(0) if self._payloads else {}
        try:
            return schema.model_validate(payload)
        except ValidationError as e:
            raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e

    def turn(self) -> AbstractAsyncContextManager["StubLLM"]:
        # 재사용할 프로세스가 없다 — self 를 그대로 감싼 no-op (ports/llm.py 계약 참고).
        return nullcontext(self)
