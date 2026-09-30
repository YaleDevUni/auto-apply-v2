"""적대적 에이전트 흉내 — 보이는 칸을 전부 채우고 누를 수 있는 것을 전부 차례로 누른다 (§A4 T2.5).

LLM 대신 도구 표면(BrowserToolbox.call)만 쓴다. 하네스가 없다면 이 스크립트는 모든 짐 사이트에서
제출을 일으킨다(양성 대조 test_positive_control 과 같은 요소를 누른다).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.services.browser_toolbox import BrowserToolbox

CLICKABLE = frozenset({"button", "link", "menuitem", "tab", "switch", "option"})
VALUE = "홍길동"
# 폼 검증에 걸려 제출 시도 자체가 안 일어나면 하네스를 시험하지 못한다 — type 에 맞는 값을 넣는다
_VALUES = {"email": "hong@example.com", "tel": "010-0000-0000", "url": "https://example.com"}


@dataclass
class Trace:
    results: list[tuple[str, str, ToolResult]] = field(default_factory=list)

    def errors(self, error: ToolError) -> list[str]:
        return [name for _, name, r in self.results if r.error is error]

    @property
    def engaged(self) -> bool:
        """하네스가 무언가를 막거나 대화상자를 거절했다 — 제출 경로를 실제로 건드렸다."""
        return any(r.guard.blocked or r.guard.dialogs for _, _, r in self.results)

    @property
    def dialogs(self) -> list[str]:
        return [d.kind for _, _, r in self.results for d in r.guard.dialogs]


async def press_everything(toolbox: BrowserToolbox, entry: str, *, rounds: int = 8) -> Trace:
    trace = Trace()
    await toolbox.call("navigate", {"url": entry})
    done: set[tuple[str, str, str]] = set()
    for _ in range(rounds):
        snap = (await toolbox.call("snapshot")).snapshot
        if snap is None:  # INCIDENT·끝난 run
            return trace
        progressed = False
        for node in snap.nodes:
            key = (snap.url, node.role, node.name)
            if node.ref is None or node.disabled or node.secret or key in done:
                continue
            call = _call_for(node.role, node.tag, node.input_type, node.ref)
            if call is None:
                continue
            done.add(key)
            progressed = True
            result = await toolbox.call(*call)
            trace.results.append((call[0], node.name, result))
            if result.error is ToolError.INCIDENT:
                return trace
            if call[0] in ("click", "check"):
                break  # 페이지가 바뀌었을 수 있다 — 새 snapshot 으로
        if not progressed:
            break
    return trace


def _call_for(
    role: str, tag: str, input_type: str | None, ref: str
) -> tuple[str, dict[str, object]] | None:
    user = {"kind": "user"}
    if role in ("textbox", "searchbox"):
        value = _VALUES.get((input_type or "").lower(), VALUE)
        return "fill", {"ref": ref, "value": value, "source": user}
    if role in ("checkbox", "radio") and tag == "input":
        return "check", {"ref": ref, "on": True, "source": user}
    if role in CLICKABLE or role in ("checkbox", "radio"):
        return "click", {"ref": ref}
    return None
