from typing import Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    """스키마 위반 시 domain.errors.LLMSchemaViolation 을 던진다 (계약)."""

    async def complete(self, prompt: str, *, max_tokens: int = 2048) -> str: ...

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048
    ) -> T: ...
