"""snapshot 을 에이전트에게 넘기기 전에 고유식별정보를 가린다 (절대 규칙 5).

snapshot 은 LLM 에 가고 run transcript 로 남는다 — 페이지에 미리 채워진 주민등록번호가 그 길로
저장되지 않게. 탐지 규칙은 domain/unique_identifiers.py 한 곳이다.
"""

from collections.abc import Iterable

from auto_apply.contracts.click import ElementDescriptor
from auto_apply.contracts.page import PageSnapshot, SnapshotNode
from auto_apply.contracts.submit_guard import SubmitTarget
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


def _redact_descriptor(d: ElementDescriptor) -> ElementDescriptor:
    return d.model_copy(
        update={
            "name": redact_text(d.name),
            "text": redact_text(d.text),
            "href": None if d.href is None else redact_text(d.href),
        }
    )


def redact_target(target: SubmitTarget) -> SubmitTarget:
    """제출 대상 기록은 저장된다 — 버튼 글자·선택자·URL 의 주민번호 꼴을 가린다."""
    return target.model_copy(
        update={
            "element": _redact_descriptor(target.element),
            "ancestors": tuple(_redact_descriptor(a) for a in target.ancestors),
            "selectors": tuple(redact_text(s) for s in target.selectors),
            "frame_url": redact_text(target.frame_url),
            "page_url": redact_text(target.page_url),
        }
    )


def redact_snapshot(snapshot: PageSnapshot) -> PageSnapshot:
    return snapshot.model_copy(
        update={
            "url": redact_text(snapshot.url),
            "title": redact_text(snapshot.title),
            "frames": tuple(redact_text(f) for f in snapshot.frames),
            "nodes": tuple(_redact_node(n) for n in snapshot.nodes),
        }
    )


HIDDEN_VALUE = "(가린 답)"


def hide_values(snapshot: PageSnapshot, hidden: Iterable[str]) -> PageSnapshot:
    """ask_user 의 가린 답이 든 칸 값을 가린다 — 에이전트·transcript 에 가지 않게.

    짧은 답("예")이면 다른 칸까지 가릴 수 있지만 값이 새는 것보다 낫다.
    """
    secrets = tuple(v for v in (h.strip() for h in hidden) if v)
    if not secrets:
        return snapshot
    nodes = tuple(
        n.model_copy(update={"value": HIDDEN_VALUE})
        if n.value is not None and any(s in n.value for s in secrets)
        else n
        for n in snapshot.nodes
    )
    return snapshot.model_copy(update={"nodes": nodes})
