from auto_apply.contracts.matching_config import MatchingConfig


class StaticMatchingConfigSource:
    """메모리에 고정된 값을 돌려준다. 테스트 대역이자, 규칙 파일 없이도

    파이프라인을 실행하기 위한 기본값(전부 빈 규칙 = 아무 공고도 통과 안 함)이다.
    """

    def __init__(self, cfg: MatchingConfig | None = None) -> None:
        self._cfg = cfg or MatchingConfig()

    async def load(self) -> MatchingConfig:
        return self._cfg
