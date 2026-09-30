"""snapshot 을 에이전트에게 넘기기 전에 고유식별정보를 가린다 (절대 규칙 5).

snapshot 은 LLM 에 가고 run transcript 로 남는다 — 페이지에 미리 채워진 주민등록번호가 그 길로
저장되지 않게. 탐지 규칙은 domain/unique_identifiers.py 한 곳이다.
"""

from auto_apply.contracts.page import PageSnapshot, SnapshotNode
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers


def redact_text(text: str) -> str:
    return redact_resident_registration_numbers(text)[0]


def _redact_node(node: SnapshotNode) -> SnapshotNode:
    update: dict[str, object] = {}
    name = redact_text(node.name)
    if name != node.name:
        update["name"] = name
    if node.value is not None and redact_text(node.value) != node.value:
        update["value"] = redact_text(node.value)
    options = tuple(redact_text(o) for o in node.options)
    if options != node.options:
        update["options"] = options
    return node.model_copy(update=update) if update else node


def redact_snapshot(snapshot: PageSnapshot) -> PageSnapshot:
    return snapshot.model_copy(
        update={
            "url": redact_text(snapshot.url),
            "title": redact_text(snapshot.title),
            "frames": tuple(redact_text(f) for f in snapshot.frames),
            "nodes": tuple(_redact_node(n) for n in snapshot.nodes),
        }
    )
