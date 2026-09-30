"""run 기록 저장 (§A5 FillLog, §A3 runs) — 되읽기와 고유식별정보 거부(절대 규칙 5)."""

import pytest

from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.fill_log import FieldLabel, FillAction, FillEntry, FillLog, FillSource
from auto_apply.domain.errors import UniqueIdentifierRejected
from auto_apply.services.run_artifacts import RunArtifacts

RRN = "900101-1234567"


def _entry(value: str) -> FillEntry:
    return FillEntry(
        seq=1, action=FillAction.FILL, ref="e1",
        field=FieldLabel(role="textbox", name="이름", url="https://jobs.example.com/a"),
        source=FillSource(kind="user"), value=value,
    )  # fmt: skip


async def test_fill_log_round_trip_and_missing_is_none():
    store = InMemoryBlobStore()
    artifacts = RunArtifacts(store)
    log = FillLog().append(_entry("홍길동"))
    await artifacts.save_fill_log("run_1", log)
    assert await artifacts.load_fill_log("run_1") == log
    assert await store.list_keys("runs/") == ["runs/run_1/fill_log.json"]
    assert await artifacts.load_fill_log("run_2") is None
    assert await artifacts.load_review("run_1") is None


async def test_identifier_smuggled_past_validation_is_not_stored():
    store = InMemoryBlobStore()
    entry = _entry("홍길동").model_copy(update={"value": RRN})
    smuggled = FillLog.model_construct(entries=(entry,))
    with pytest.raises(UniqueIdentifierRejected):
        await RunArtifacts(store).save_fill_log("run_1", smuggled)
    assert await store.list_keys("runs/") == []
