"""snapshot·FillLog DTO 의 불변식 — 어댑터가 실수해도 비밀값·고유식별정보가 실리지 않는다."""

import pytest
from pydantic import ValidationError

from auto_apply.contracts.fill_log import FieldLabel, FillAction, FillEntry, FillLog, FillSource
from auto_apply.contracts.page import PageSnapshot, SnapshotNode

FIELD = FieldLabel(role="textbox", name="이름", url="http://x.test/")
SRC = FillSource(kind="user")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "node",
    [
        {"role": "textbox", "tag": "input", "input_type": "password", "value": "pw"},
        {"role": "textbox", "tag": "input", "autocomplete": "one-time-code", "value": "123456"},
        {"role": "textbox", "tag": "input", "secret": True, "value": "pw"},
    ],
)
def test_secret_nodes_drop_values_even_if_an_adapter_sends_them(node):
    parsed = SnapshotNode.model_validate(node)
    assert parsed.secret is True and parsed.value is None


def test_snapshot_lookup():
    snap = PageSnapshot(url="u", nodes=(SnapshotNode(role="text", name="t"),
                                        SnapshotNode(ref="e3", role="textbox")))  # fmt: skip
    assert snap.node("e3") is not None and snap.node("e1") is None


def test_fill_entry_shapes():
    FillEntry(seq=1, action=FillAction.FILL, ref="e1", field=FIELD, source=SRC, value="v")
    FillEntry(seq=1, action=FillAction.FILL, ref="e1", field=FIELD, source=SRC, withheld=True)
    FillEntry(seq=1, action=FillAction.CHECK, ref="e1", field=FIELD, source=SRC, checked=False)
    FillEntry(seq=1, action=FillAction.UPLOAD, ref="e1", field=FIELD, document_id="doc_1")


@pytest.mark.parametrize(
    "bad",
    [
        {"action": FillAction.FILL, "source": SRC},  # 값 없음
        {"action": FillAction.CHECK, "source": SRC},
        {"action": FillAction.UPLOAD, "document_id": "doc_1", "source": SRC},
        {"action": FillAction.FILL, "value": "v"},  # 근거 없음
        {"action": FillAction.FILL, "source": SRC, "value": "v", "withheld": True},
        {"action": FillAction.FILL, "source": SRC, "value": "900101-1234567"},  # 절대 규칙 5
    ],
)
def test_fill_entry_rejects(bad):
    with pytest.raises(ValidationError):
        FillEntry(seq=1, ref="e1", field=FIELD, **bad)


def test_fill_log_appends_immutably():
    log = FillLog()
    entry = FillEntry(seq=log.next_seq, action=FillAction.FILL, ref="e1", field=FIELD, source=SRC,
                      value="v")  # fmt: skip
    longer = log.append(entry)
    assert log.entries == () and longer.entries == (entry,) and longer.next_seq == 2
