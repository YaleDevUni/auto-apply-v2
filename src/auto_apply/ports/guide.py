from typing import Protocol


class GuideSource(Protocol):
    """`config/resume_guide.{platform}.md`(REVISE/general 로 사람이 승인한 규칙이 쌓이는 곳)를

    플랫폼별로 읽고 쓴다 — 원티드/사람인처럼 플랫폼마다 이력서 포맷·관례가 달라 가이드도 갈릴 수
    있다. `FactSource`/`ProfileSource`와 같은 이유로 캐시 없이 매번 새로 읽는다 — patch 가
    반영되면 바로 다음 생성부터 보여야 한다. `save`는 `apply_guide_patch` activity
    (§ domain/guide_patch.py) 가 사람의 2차 승인 뒤에만 부른다 — 이 포트 자체는 그 정책을 모른다.
    """

    async def get(self, platform: str) -> str: ...

    async def save(self, platform: str, text: str) -> None: ...
