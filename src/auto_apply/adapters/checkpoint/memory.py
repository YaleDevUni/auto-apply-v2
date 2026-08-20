"""CheckpointStore 테스트 대역 — 프로세스 경계를 안 넘는 단위/워크플로우 테스트에서 쓴다."""


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._decisions: dict[str, bool] = {}

    async def record_decision(self, nonce: str, *, approved: bool) -> None:
        self._decisions[nonce] = approved

    async def get_decision(self, nonce: str) -> bool | None:
        return self._decisions.get(nonce)
