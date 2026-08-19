from typing import Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    """스키마 위반 시 domain.errors.LLMSchemaViolation 을 던진다 (계약).

    `cache_prefix`는 선택 힌트다 — 실제로 모델에 보내는 내용은 `cache_prefix + prompt`이고,
    `cache_prefix`가 안정적인(반복 호출에 걸쳐 바이트 단위로 동일한) 내용이면 그 경계에 캐시
    브레이크포인트를 두라는 신호다. 무시하고 단순 문자열 결합으로 처리해도 계약 위반이
    아니다 — 결과의 정확성에는 영향을 주지 않는다.

    [[claude-cli-prompt-cache-redesign]]에서 실측: prefix caching이 붙으려면 이 경계가
    "명시적인 콘텐츠 블록 분리"여야 한다 — 하나의 문자열 안에서 앞부분만 같고 뒷부분이
    달라지는 식으로는(순서를 어떻게 배치하든) 캐시가 전혀 안 붙는다. `ClaudeCodeCliLLM`은
    이 신호로 실제 블록을 나눠 보낸다(§11.2). `SimpleResumeGenerator`의 재프롬프트 루프처럼
    원본 프롬프트가 여러 시도에 걸쳐 그대로 유지되는 경우가 전형적인 사용처다.
    """

    async def complete(
        self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> str: ...

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T: ...
