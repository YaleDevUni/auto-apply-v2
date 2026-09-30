"""메모리 DOM 관찰 — L5 글자 줄과 D17 마지막 단계 신호 재료 (§A4, 테스트 대역).

실제 드라이버(guard_script.READ_PAGE)와 같은 기준으로 센다: 입력칸은 체크박스를 빼고 파일 입력은
숨겨져 있어도 센다, 제출 버튼은 폼 안의 보이는 submit 컨트롤.
"""

from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.contracts.submit_guard import PageText
from auto_apply.domain import page_elements as rules

_TEXT_ROLES = frozenset({"text", "heading", "status", "alert", "button", "link"})


def observe(doc: FakeDocument, url: str) -> PageText:
    lines = [e.name for e in doc.elements if e.visible and e.name and e.role in _TEXT_ROLES]
    return PageText(
        urls=(url, *doc.frames),
        lines=tuple(lines),
        inputs=sum(1 for e in doc.elements if _editable(e)),
        submitters=sum(1 for e in doc.elements if e.visible and _submitter(e)),
        progress=doc.progress,
    )


def _editable(el: FakeElement) -> bool:
    """실제 드라이버의 입력칸 셈과 같은 기준 — 체크박스 제외, 파일 입력은 숨겨도 센다."""
    if rules.is_file_input(el.tag, el.type):
        return True
    kind = rules.input_type(el.tag, el.type)
    return el.visible and (
        rules.accepts_text(el.tag, el.type)
        or rules.is_native_select(el.tag)
        or kind in ("radio", "password")
    )


def _submitter(el: FakeElement) -> bool:
    kind = (el.type or ("submit" if el.tag == "button" else "text")).lower()
    return el.in_form and (
        (el.tag == "button" and kind == "submit")
        or (el.tag == "input" and kind in ("submit", "image"))
    )
