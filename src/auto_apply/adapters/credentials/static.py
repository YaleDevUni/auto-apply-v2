from auto_apply.ports.credentials import Credential


class StaticCredentialSource:
    """`ports/credentials.py`의 테스트 대역. 고정된 dict 를 그대로 조회한다."""

    def __init__(self, data: dict[str, Credential] | None = None) -> None:
        self._data = data or {}

    async def get(self, key: str) -> Credential | None:
        return self._data.get(key)
