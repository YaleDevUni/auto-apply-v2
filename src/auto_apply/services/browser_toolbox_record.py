"""도구 결과를 남기는 모양 — FillLog 항목과 입력 검증 실패 설명 (§A5).

둘 다 LLM 이 준 값·페이지 글자를 싣는 자리라 고유식별정보를 거른다(절대 규칙 5).
"""

from pydantic import ValidationError

from auto_apply.contracts.fill_log import FieldLabel, FillAction, FillEntry, FillSource
from auto_apply.contracts.page import PageSnapshot, SnapshotNode
from auto_apply.domain.unique_identifiers import contains_resident_registration_number
from auto_apply.services.browser_toolbox_redact import redact_text


def fill_entry(
    seq: int,
    action: FillAction,
    snapshot: PageSnapshot,
    node: SnapshotNode,
    source: FillSource | None,
    *,
    value: str | None = None,
    checked: bool | None = None,
    document_id: str | None = None,
) -> FillEntry:
    """`node`(snapshot 의 한 줄)에 한 동작의 기록.

    값에 주민등록번호 꼴이 있으면 값 없이 `withheld` — 승인 뒤 재입력 때 다시 묻는다.
    """
    assert node.ref is not None
    frames = snapshot.frames
    url = frames[node.frame] if node.frame < len(frames) else snapshot.url
    withheld = value is not None and contains_resident_registration_number(value)
    return FillEntry(
        seq=seq,
        action=action,
        ref=node.ref,
        field=FieldLabel(role=node.role, name=node.name, url=redact_text(url)),
        source=source,
        value=None if withheld else value,
        checked=checked,
        document_id=document_id,
        withheld=withheld,
    )


def describe_validation_error(e: ValidationError) -> str:
    """어느 인자가 왜 틀렸는지만 — 입력값은 싣지 않는다."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(args)'}: {err['msg']}"
        for err in e.errors(include_input=False, include_url=False)
    )
