class StaticGuideSource:
    """네트워크·파일 없이 파이프라인을 돌리기 위한 어댑터. 테스트 대역."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    async def get(self) -> str:
        return self._text

    async def save(self, text: str) -> None:
        self._text = text
