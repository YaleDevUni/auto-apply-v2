"""AgentBrowserExecutor 전용 selector 분류 — 순수 함수 (domain 계층, §계층 규칙).

agent-browser CLI 는 raw CSS selector 를 받지만 브라우저 네이티브 `document.querySelector`로
해석한다. Playwright 가 CSS 위에 얹은 확장 문법(`:has-text()`, `:text-is()`)이나 엔진-프리픽스
문법(`text=`, `role=[name=]`, ...)은 그 querySelector 로 못 읽는다 — 이게 두 실행기의
"접근성 트리 해석 차이"(메모리 agent-browser-executor-design)의 정체다. 이 모듈은
`resolve_selector()`가 `{value}`를 치환한 뒤의 selector 문자열 하나를 셋 중 하나로 분류한다.
실제 CLI 호출/JS eval 실행은 adapters/executor/agent_browser.py 가 한다(domain 은 순수해야
한다) — 여기서는 "어떻게 실행할지"만 결정하고 "실행"은 하지 않는다.

지원 범위는 현재 실제로 쓰이는 selector 형태(var/recipes/*.json)를 근거로 잡았다 — 전체
Playwright 선택자 문법을 재현하려는 시도가 아니다. `[role|text|label|placeholder|alt|title|
testid]=` 프리픽스와 `:has-text()`/`:text-is()`만 다룬다.
"""

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FindLocator:
    """Playwright 엔진-프리픽스(`text=`, `role=[name=]`, `label=`, ...) → agent-browser

    `find <locator> <value> <action>` 매핑."""

    locator: str  # role | text | label | placeholder | alt | title | testid
    value: str
    name: str | None = None  # role 전용, --name
    exact: bool = False  # text="..." 처럼 따옴표로 감싼 값이면 --exact


@dataclass(frozen=True)
class TextFilterTarget:
    """`:has-text()`/`:text-is()` — agent-browser querySelector 가 못 읽어서 JS eval 로 찾는다."""

    base_selector: str  # has-text/text-is 앞의 CSS (없으면 "*")
    exact: bool  # text-is 면 True(완전 일치), has-text 면 False(부분 포함)
    text: str
    descendant_selector: str | None = (
        None  # 뒤에 남는 결합자, 예: 'li:has-text("x") label'의 'label'
    )

    def to_js_finder(self) -> str:
        """이 selector 가 가리키는 엘리먼트를 찾는 JS 표현식(없으면 null) — 문자열만 만들고

        실행은 adapter 가 `eval`로 한다(domain 은 순수해야 한다)."""
        return (
            "(() => { "
            f"const base = {json.dumps(self.base_selector)}; "
            f"const text = {json.dumps(self.text)}; "
            f"const exact = {json.dumps(self.exact)}; "
            f"const rest = {json.dumps(self.descendant_selector or '')}; "
            "for (const el of document.querySelectorAll(base)) { "
            "const t = (el.textContent || '').trim(); "
            "const hit = exact ? t === text.trim() : t.includes(text); "
            "if (!hit) continue; "
            "if (!rest) return el; "
            "const found = el.querySelector(rest); "
            "if (found) return found; "
            "} "
            "return null; "
            "})()"
        )


# 그 외엔 raw CSS 로 그대로 agent-browser 명령에 넘긴다.
ResolvedSelector = FindLocator | TextFilterTarget | str

_ROLE_RE = re.compile(r'^role=([A-Za-z]+)(?:\[name="((?:[^"\\]|\\.)*)"(?:\s+i)?\])?$')
_ENGINE_RE = re.compile(
    r'^(text|label|placeholder|alt|title|testid)=(?:"((?:[^"\\]|\\.)*)"|(.*))$', re.DOTALL
)
_HAS_TEXT_RE = re.compile(
    r'^(?P<base>.*?):(?P<kind>has-text|text-is)\("(?P<text>(?:[^"\\]|\\.)*)"\)\s*(?P<rest>.*)$',
    re.DOTALL,
)


def classify_selector(selector: str) -> ResolvedSelector:
    m = _ROLE_RE.match(selector)
    if m:
        role, name = m.groups()
        return FindLocator(locator="role", value=role, name=_unescape(name) if name else None)

    m = _ENGINE_RE.match(selector)
    if m:
        locator, quoted, bare = m.groups()
        if quoted is not None:
            return FindLocator(locator=locator, value=_unescape(quoted), exact=True)
        return FindLocator(locator=locator, value=bare)

    m = _HAS_TEXT_RE.match(selector)
    if m:
        base = m.group("base").strip() or "*"
        return TextFilterTarget(
            base_selector=base,
            exact=m.group("kind") == "text-is",
            text=_unescape(m.group("text")),
            descendant_selector=m.group("rest").strip() or None,
        )

    return selector


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")
