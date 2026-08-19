from typing import Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    """스키마 위반 시 domain.errors.LLMSchemaViolation 을 던진다 (계약).

    `cache_key`는 선택 힌트다 — 같은 키로 들어온 연속 호출을 하나의 대화로 묶어도 되면
    묶으라는 신호일 뿐, 결과의 정확성에는 영향을 주지 않는다(무시해도 계약 위반이 아니다).
    `ClaudeCodeCliLLM`은 이걸로 세션을 이어붙여 프롬프트 캐시를 태운다(§11.2) — 보통
    user_id 를 넘겨서 같은 사용자의 여러 공고에 걸쳐 반복되는 Fact/Profile 프리픽스가
    캐시로 읽히게 한다.
    """

    async def complete(
        self, prompt: str, *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> str: ...

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> T: ...
