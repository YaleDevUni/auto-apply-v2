"""제출 클릭 분류(§A4 L2)와 완료 감지(§A4 L5) — 순수 함수. 어휘는 `submit_vocabulary.py`.

분류는 허용 목록 방식이다: 위험 신호(제출 컨트롤·기본 버튼·dialog 긍정·제출 어휘)가 하나라도 있으면
Risky, 없어도 **안전하다고 볼 근거**(입력 요소·실제 href 링크·안전 어휘)가 있어야만 Safe. 나머지는
전부 Risky(UNRECOGNIZED) — 판단이 애매하면 닫힌 쪽으로 실패한다. Risky 는 막히는 게 아니라 strict
창(L3)에서 실행될 뿐이라 과잉 판정의 비용은 작고, 과소 판정은 제출 사고다.
"""

import re
import unicodedata
from urllib.parse import unquote

from auto_apply.contracts.click import (
    ClickRisk,
    ElementDescriptor,
    RiskyClick,
    SafeBasis,
    SafeClick,
)
from auto_apply.domain import submit_vocabulary as vocab

# NFKC 가 지우지 않는 zero-width 문자 — 글자 사이에 끼워 어휘를 피하는 라벨을 막는다.
_ZERO_WIDTH: dict[int, None] = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD))
_CURLY_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "`": "'"})
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
_URL_SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):")
# 브라우저는 URL 에서 탭·개행을 지운다 — "java\nscript:" 도 javascript: 로 본다.
_URL_STRIPPED = re.compile(r"[\x00-\x20\x7f]")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).casefold()


def _compact(folded: str) -> str:
    """글자·숫자만 남긴다 — "제 출"·"지원-하기"·"S u b m i t" 를 한 덩어리로."""
    return "".join(ch for ch in folded if ch.isalnum())


def _ascii_lower(value: str | None) -> str:
    # HTML 열거형 속성(type·role)은 ASCII 대소문자만 무시한다. 전각·공백이 섞인 값은 브라우저에게도
    # 알 수 없는 값이라 NFKC 로 접지 않는다 — 접으면 button 의 "알 수 없는 type = submit" 을 놓친다.
    if value is None:
        return ""
    return "".join(ch.lower() if ch.isascii() else ch for ch in value)


def _is_submit_control(tag: str, type_: str | None, in_form: bool) -> bool:
    if tag == "input":
        return type_ in vocab.SUBMIT_INPUT_TYPES
    if tag == "button":
        if type_ is None:
            return in_form  # type 없는 button 은 submit — 폼이 있을 때만 제출이 일어난다
        return type_ not in vocab.NON_SUBMIT_BUTTON_TYPES
    return False


def _is_inert(tag: str, type_: str | None, role: str) -> bool:
    if tag == "input":
        return type_ is None or type_ in vocab.INERT_INPUT_TYPES
    return tag in vocab.INERT_TAGS or role in vocab.INERT_ROLES


def _is_navigation_link(tag: str, role: str, href: str | None) -> bool:
    if tag != "a" or role not in ("", "link") or not href:
        return False
    url = _URL_STRIPPED.sub("", _ascii_lower(href))
    if not url or url.startswith("#"):
        return False
    scheme = _URL_SCHEME.match(url)
    return scheme is None or scheme.group(1) in ("http", "https")


def _risky_word(folded: str) -> str | None:
    compact = _compact(folded)
    for word in (*vocab.RISKY_KO, *vocab.RISKY_EN_STEMS):
        if word in compact:
            return word
    return next((t for t in _ASCII_TOKEN.findall(folded) if t in vocab.RISKY_EN_TOKENS), None)


def _safe_word(folded: str) -> str | None:
    compact = _compact(folded)
    # 한글 음절·ASCII 가 아닌 글자(키릴 닮은꼴·낱자모·다른 언어)가 섞이면 어휘 판정을 믿을 수 없다.
    if any(not ch.isascii() and not "가" <= ch <= "힣" for ch in compact):
        return None
    ko = next((w for w in vocab.SAFE_KO if w in compact), None)
    return ko or next((t for t in _ASCII_TOKEN.findall(folded) if t in vocab.SAFE_EN_TOKENS), None)


def classify_click(element: ElementDescriptor) -> SafeClick | RiskyClick:
    tag = _ascii_lower(element.tag).strip()
    type_ = None if element.type is None else _ascii_lower(element.type)
    role = (_ascii_lower(element.role).split() or [""])[0]

    if _is_submit_control(tag, type_, element.in_form):
        return RiskyClick(reason=ClickRisk.SUBMIT_TYPE, detail=f"{tag}[type={element.type}]")
    if element.is_form_default_button:
        return RiskyClick(reason=ClickRisk.FORM_DEFAULT_BUTTON)

    parts = [_fold(p) for p in dict.fromkeys((element.name, element.text)) if p.strip()]
    if element.in_dialog:
        hit = next((c for c in map(_compact, parts) if c in vocab.DIALOG_AFFIRMATIVE), None)
        if hit:
            return RiskyClick(reason=ClickRisk.DIALOG_CONFIRM, detail=hit)

    label = " ".join(parts)
    href = _fold(unquote(element.href)) if element.href else ""
    word = _risky_word(label) or _risky_word(href)
    if word:
        return RiskyClick(reason=ClickRisk.SUBMIT_WORD, detail=word)

    if _is_inert(tag, type_, role):
        return SafeClick(basis=SafeBasis.INERT_CONTROL, detail=tag)
    if _is_navigation_link(tag, role, element.href):
        return SafeClick(basis=SafeBasis.NAVIGATION_LINK)
    safe = _safe_word(label)
    if safe:
        return SafeClick(basis=SafeBasis.SAFE_WORD, detail=safe)
    return RiskyClick(reason=ClickRisk.UNRECOGNIZED)


def detect_completion(*, url: str = "", text: str = "") -> str | None:
    """클릭 뒤 URL·본문이 지원 완료처럼 보이면 근거 문자열, 아니면 None (§A4 L5).

    근거가 나오면 호출자는 run 을 즉시 멈추고 INCIDENT 를 남긴다 — 오탐은 run 중단(안전)이고
    미탐은 사고 은폐라, 폼 안내문에 걸리지 않는 선에서 넓게 잡는다. 클릭 전에도 이미 나오던 근거인지
    (페이지 고정 문구) 가르는 것은 클릭 전후를 아는 호출자(SubmitGuard) 몫이다.
    """
    folded = _fold(text).translate(_CURLY_QUOTES)
    ko = vocab.COMPLETION_KO.search(_compact(folded))
    if ko:
        return f"text:{ko.group(0)}"
    en = vocab.COMPLETION_EN.search(" ".join(folded.split()))
    if en:
        return f"text:{en.group(0)}"
    path = _fold(unquote(url)).split("://", 1)[-1].partition("/")[2]
    token = next((t for t in _ASCII_TOKEN.findall(path) if t in vocab.COMPLETION_URL_TOKENS), None)
    return f"url:{token}" if token else None
