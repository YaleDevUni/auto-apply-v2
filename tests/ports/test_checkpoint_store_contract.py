"""CheckpointStore contract test (§ supervised-checkpoint-design).

계약: 결정이 없으면 `get_decision`은 None, `record_decision`은 nonce 별로 승인/거절을
기억하고, 같은 nonce 에 다시 걸어도(재전송) 값이 그대로 남는다(멱등).
"""

from pathlib import Path

import pytest

from auto_apply.adapters.checkpoint.file import FileCheckpointStore
from auto_apply.adapters.checkpoint.memory import InMemoryCheckpointStore
from auto_apply.ports.checkpoint_store import CheckpointStore


@pytest.fixture(params=["memory", "file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> CheckpointStore:
    if request.param == "memory":
        return InMemoryCheckpointStore()
    return FileCheckpointStore(tmp_path)


async def test_unknown_nonce_returns_none(store: CheckpointStore) -> None:
    assert await store.get_decision("nonce_1") is None


async def test_record_and_get_approved(store: CheckpointStore) -> None:
    await store.record_decision("nonce_1", approved=True)
    assert await store.get_decision("nonce_1") is True


async def test_record_and_get_declined(store: CheckpointStore) -> None:
    await store.record_decision("nonce_1", approved=False)
    assert await store.get_decision("nonce_1") is False


async def test_record_decision_is_idempotent(store: CheckpointStore) -> None:
    await store.record_decision("nonce_1", approved=True)
    await store.record_decision("nonce_1", approved=True)
    assert await store.get_decision("nonce_1") is True


async def test_decisions_are_isolated_per_nonce(store: CheckpointStore) -> None:
    await store.record_decision("nonce_1", approved=True)
    await store.record_decision("nonce_2", approved=False)
    assert await store.get_decision("nonce_1") is True
    assert await store.get_decision("nonce_2") is False
