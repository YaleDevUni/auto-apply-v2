"""CheckpointWaiter — 승인/거절/타임아웃 세 갈래 + 스크린샷 업로드 (§ supervised-checkpoint-design).

실제 시간을 기다리므로 timeout/poll_interval 을 아주 짧게 잡는다. 프로세스 경계를 넘는
nonce 공유 자체는 tests/ports/test_checkpoint_store_contract.py 가 이미 검증했다 — 여기서는
CheckpointWaiter 가 그 store 를 올바른 순서로 부르는지만 본다.
"""

from datetime import timedelta

import pytest

from auto_apply.adapters.checkpoint.memory import InMemoryCheckpointStore
from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.executor._checkpoint import CheckpointWaiter
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.domain.errors import CheckpointDeclined

_SHORT = timedelta(milliseconds=200)
_POLL = timedelta(milliseconds=20)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []
        self._seq = 0

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        self.requests.append(req)
        self._seq += 1
        return DecisionTicket(ticket_id=f"tkt_{self._seq}", nonce=f"nonce_{self._seq}")

    async def notify(self, event: NotifyEvent) -> None:
        pass


def _waiter(notifier, store, blob) -> CheckpointWaiter:
    return CheckpointWaiter(notifier, store, blob, UuidIdGen(), timeout=_SHORT, poll_interval=_POLL)


async def test_uploads_screenshot_and_requests_decision():
    notifier = _RecordingNotifier()
    store = InMemoryCheckpointStore()
    blob = InMemoryBlobStore()
    waiter = _waiter(notifier, store, blob)
    await store.record_decision("nonce_1", approved=True)

    await waiter.wait(
        application_id="app_1",
        attempt=1,
        label="00:submit",
        screenshot=b"fake-png",
        heartbeat=None,
    )

    assert len(notifier.requests) == 1
    req = notifier.requests[0]
    assert req.checkpoint is True
    assert req.artifact_url is not None
    assert await blob.get(req.artifact_url) == b"fake-png"


async def test_approved_decision_returns():
    notifier = _RecordingNotifier()
    store = InMemoryCheckpointStore()
    blob = InMemoryBlobStore()
    waiter = _waiter(notifier, store, blob)
    await store.record_decision("nonce_1", approved=True)

    await waiter.wait(
        application_id="app_1", attempt=1, label="x", screenshot=b"x", heartbeat=None
    )  # 예외 없이 끝나야 한다


async def test_declined_decision_raises_checkpoint_declined():
    notifier = _RecordingNotifier()
    store = InMemoryCheckpointStore()
    blob = InMemoryBlobStore()
    waiter = _waiter(notifier, store, blob)
    await store.record_decision("nonce_1", approved=False)

    with pytest.raises(CheckpointDeclined):
        await waiter.wait(
            application_id="app_1", attempt=1, label="x", screenshot=b"x", heartbeat=None
        )


async def test_timeout_raises_checkpoint_declined():
    notifier = _RecordingNotifier()
    store = InMemoryCheckpointStore()  # 아무 결정도 안 남긴다 — 계속 None
    blob = InMemoryBlobStore()
    waiter = _waiter(notifier, store, blob)

    with pytest.raises(CheckpointDeclined):
        await waiter.wait(
            application_id="app_1", attempt=1, label="x", screenshot=b"x", heartbeat=None
        )


async def test_heartbeats_while_polling():
    notifier = _RecordingNotifier()
    store = InMemoryCheckpointStore()
    blob = InMemoryBlobStore()
    waiter = _waiter(notifier, store, blob)
    calls: list[str] = []

    with pytest.raises(CheckpointDeclined):
        await waiter.wait(
            application_id="app_1",
            attempt=1,
            label="x",
            screenshot=b"x",
            heartbeat=calls.append,
        )

    assert calls, "타임아웃까지 대기하는 동안 최소 한 번은 heartbeat 를 호출해야 한다"
