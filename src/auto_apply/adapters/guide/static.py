class StaticGuideSource:
    """네트워크·파일 없이 파이프라인을 돌리기 위한 어댑터. 테스트 대역.

    `text`는 아직 아무 플랫폼도 `save`로 값을 채우지 않았을 때의 기본값이다 — 플랫폼별로 갈리는
    걸 굳이 검증하지 않는 대다수 테스트가 플랫폼을 안 넘겨도 되게 해준다(`StaticFactSource`가
    `user_id`로 거르는 것과 같은 자리에, 여기는 "아직 아무것도 안 쌓인 상태"의 기본값이 필요해
    한 단계 더 있다).
    """

    def __init__(self, text: str = "") -> None:
        self._default = text
        self._texts: dict[str, str] = {}

    async def get(self, platform: str) -> str:
        return self._texts.get(platform, self._default)

    async def save(self, platform: str, text: str) -> None:
        self._texts[platform] = text
