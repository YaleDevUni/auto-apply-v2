from typing import Protocol


class GuideSource(Protocol):
    """`resume_guide.{platform}.md`(사람이 승인한 이력서 작성 규칙)를 키별로 읽고 쓴다.

    `FactSource`/`ProfileSource`와 같은 이유로 캐시 없이 매번 새로 읽는다 — 가이드가 바뀌면
    바로 다음 생성부터 보여야 한다. v3 가이드(§A8, DB·버전·scope)로 M6 에서 대체된다.
    """

    async def get(self, platform: str) -> str: ...

    async def save(self, platform: str, text: str) -> None: ...
