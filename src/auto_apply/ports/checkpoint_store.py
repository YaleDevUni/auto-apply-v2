from typing import Protocol


class CheckpointStore(Protocol):
    """SUPERVISED 체크포인트 승인/거절 (§ supervised-checkpoint-design).

    nonce 발급 프로세스(worker, activity 안에서 기다린다)와 검증 프로세스(webhook 서버/
    리스너)가 갈라진다 — `ports/notifier.py`/`telegram/bridge.py`의 nonce 와 같은 이유다.
    다만 그 nonce 들과 달리 여기서 기다리는 건 워크플로우가 아니라 activity 자신이라
    Temporal signal 로 못 받는다 — 그래서 프로세스 경계를 넘는 공유 저장소가 따로 필요하다.

    `record_decision`은 멱등해야 한다(같은 nonce 에 다시 걸 수 있어야 재전송을 버틴다).
    `get_decision`은 아직 결정이 안 났으면 `None`을 돌려준다.
    """

    async def record_decision(self, nonce: str, *, approved: bool) -> None: ...

    async def get_decision(self, nonce: str) -> bool | None: ...
