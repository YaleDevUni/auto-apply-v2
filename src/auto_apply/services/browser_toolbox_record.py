"""도구 결과를 남기는 모양 — FillLog 항목·실패 결과·입력 검증 실패 설명 (§A5, §A4).

LLM 이 준 값·페이지 글자를 싣는 자리라 고유식별정보를 거른다(절대 규칙 5).
"""

from pydantic import ValidationError

from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.fill_log import FieldLabel, FillAction, FillEntry, FillSource
from auto_apply.contracts.page import PageSnapshot, SnapshotNode
from auto_apply.contracts.submit_guard import GuardReport
from auto_apply.domain.unique_identifiers import contains_resident_registration_number
from auto_apply.services.browser_toolbox_redact import redact_text

# 막힌 클릭이 최종 제출인지 단계 이동인지 하네스는 가르지 않는다 (§A4 L6 확장) — 그대로 알린다.
SUBMIT_BLOCKED_MESSAGE = (
    "이 동작은 제출 동작으로 판정돼 하네스가 막았다(최종 제출인지 단계 이동인지는 구분하지 않는다)."
    " 입력을 모두 마쳤으면 ready_for_review(submit_ref=누르려던 ref) 로 사람 승인을 받는다."
    " 승인 뒤 누르는 것은 하네스다."
)
INCIDENT_MESSAGE = (
    "지원 완료로 보이는 화면이 나타나 run 을 멈췄다(§A4 L5). 제출이 이미 일어났을 수 있다"
    " — 사람이 확인한다. 어떤 도구도 더 받지 않는다."
)


def fail(tool: str, error: ToolError, message: str, guard: GuardReport | None = None) -> ToolResult:
    return ToolResult(
        tool=tool, ok=False, error=error, message=message, guard=guard or GuardReport()
    )


def blocked_result(tool: str, guard: GuardReport) -> ToolResult:
    return fail(tool, ToolError.SUBMIT_BLOCKED, SUBMIT_BLOCKED_MESSAGE, guard)


def incident_result(tool: str, guard: GuardReport) -> ToolResult:
    return fail(tool, ToolError.INCIDENT, INCIDENT_MESSAGE, guard)


def fill_entry(
    seq: int,
    step: int,
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
        step=step,
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
