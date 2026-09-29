from contextlib import AbstractAsyncContextManager
from typing import Protocol

from pydantic import BaseModel


class LLMCallable(Protocol):
    """`LLMClient.turn()`이 돌려주는 최소 계약 — `complete`/`structured`만 있으면 된다.

    `LLMClient`를 그대로 요구하면(turn() 도 있어야 함) `turn()`의 반환값이 다시 `turn()`을
    가져야 하는 재귀적 요구가 생긴다 — `turn()`으로 받은 객체는 "이 턴 안에서 호출할 것"만
    쓰면 되므로 그 요구는 불필요하다.
    """

    async def complete(
        self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> str: ...

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T: ...


class LLMClient(LLMCallable, Protocol):
    """스키마 위반 시 domain.errors.LLMSchemaViolation 을 던진다 (계약).

    `cache_prefix`는 선택 힌트다 — 실제로 모델에 보내는 내용은 `cache_prefix + prompt`이고,
    `cache_prefix`가 안정적인(반복 호출에 걸쳐 바이트 단위로 동일한) 내용이면 그 경계에 캐시
    브레이크포인트를 두라는 신호다. 무시하고 단순 문자열 결합으로 처리해도 계약 위반이
    아니다 — 결과의 정확성에는 영향을 주지 않는다.

    [[claude-cli-prompt-cache-redesign]]에서 실측: prefix caching이 붙으려면 이 경계가
    "명시적인 콘텐츠 블록 분리"여야 한다 — 하나의 문자열 안에서 앞부분만 같고 뒷부분이
    달라지는 식으로는(순서를 어떻게 배치하든) 캐시가 전혀 안 붙는다. `ClaudeCodeCliLLM`은
    이 신호로 실제 블록을 나눠 보낸다(D5). `SimpleResumeGenerator`의 재프롬프트 루프처럼
    원본 프롬프트가 여러 시도에 걸쳐 그대로 유지되는 경우가 전형적인 사용처다.
    """

    def turn(self) -> AbstractAsyncContextManager[LLMCallable]:
        """여러 `complete`/`structured` 호출을 하나의 논리적 대화 턴으로 묶는다 — 짧은 호출이
        잦을 때 호출당 기동 비용을 없애려는 것이다.

        구현체가 프로세스 등 재사용 가능한 자원을 갖고 있으면 여기서 재사용해 호출마다 드는
        고정비용을 줄일 수 있다(`ClaudeCodeCliLLM.turn()` 실측: 프로세스 재사용 시 ~3.5초의
        기동 비용이 최초 1회로 줄고, 이후 호출은 순수 모델 응답 시간만 든다). 기본 계약은
        "재사용 안 해도 무방"이다 — 그냥 self 를 감싼 no-op 컨텍스트 매니저를 반환해도 계약
        위반이 아니다. 단, 재사용을 하든 안 하든 turn() 으로 받은 객체 안에서의 여러 호출은
        서로 독립된 대화로 취급돼야 한다(직전 호출 내용이 다음 호출에 자동으로 새어들면 안
        된다) — 자원을 재사용하는 구현체는 그 경계(예: `/clear`)를 스스로 책임진다.
        """
        ...
