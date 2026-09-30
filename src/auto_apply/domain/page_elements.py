"""BrowserToolbox 가 요소에 무엇을 해도 되는지 (§A5, 절대 규칙 3).

실제 브라우저 어댑터와 테스트 대역이 같은 규칙을 쓰도록 순수 함수로 둔다. 값은 DOM 에서 읽은
그대로(대소문자·공백 포함) 받고 여기서 정규화한다.
"""

# 자격증명·인증 코드 칸. 값을 읽지도(snapshot) 쓰지도(fill) 않는다 — 비밀번호 타이핑 금지,
# SMS 본인인증 대리 금지(절대 규칙 3). type=text 로 바꿔 보이게 하는 "보기" 토글도
# autocomplete 로 잡는다.
_SECRET_AUTOCOMPLETE = frozenset({"current-password", "new-password", "one-time-code"})

# 문자열을 받아 적는 input type. HTML 기본값(속성 없음·알 수 없는 값)은 text 다.
_TEXT_INPUT_TYPES = frozenset(
    {
        "text",
        "email",
        "tel",
        "url",
        "number",
        "search",
        "date",
        "datetime-local",
        "month",
        "week",
        "time",
    }
)
# 문자열 입력이 아닌 input type — 여기 없으면 HTML 처럼 text 로 본다.
_NON_TEXT_INPUT_TYPES = frozenset(
    {
        "password",
        "checkbox",
        "radio",
        "file",
        "submit",
        "button",
        "reset",
        "image",
        "hidden",
        "range",
        "color",
    }
)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def input_type(tag: str, type_attr: str | None) -> str | None:
    """input 의 실효 type (HTML 규칙: 없거나 모르는 값이면 text). input 이 아니면 None."""
    if _norm(tag) != "input":
        return None
    t = _norm(type_attr)
    return t if t in _TEXT_INPUT_TYPES or t in _NON_TEXT_INPUT_TYPES else "text"


def is_secret_field(tag: str, type_attr: str | None, autocomplete: str | None) -> bool:
    if input_type(tag, type_attr) == "password":
        return True
    tokens = set(_norm(autocomplete).split())
    return bool(tokens & _SECRET_AUTOCOMPLETE)


def accepts_text(tag: str, type_attr: str | None, *, contenteditable: bool = False) -> bool:
    """`fill` 대상인가 — 한 줄·여러 줄 텍스트 칸과 contenteditable 만."""
    t = _norm(tag)
    if t == "textarea" or contenteditable:
        return True
    return input_type(t, type_attr) in _TEXT_INPUT_TYPES


def is_toggle(tag: str, type_attr: str | None) -> bool:
    """`check` 대상 — 네이티브 checkbox·radio 만. role=checkbox 인 div 는 클릭 핸들러가 무엇이든
    할 수 있어 클릭 분류(§A4 L2)를 거치는 `click` 몫이다."""
    return input_type(tag, type_attr) in ("checkbox", "radio")


def is_radio(tag: str, type_attr: str | None) -> bool:
    return input_type(tag, type_attr) == "radio"


def is_file_input(tag: str, type_attr: str | None) -> bool:
    return input_type(tag, type_attr) == "file"


def is_native_select(tag: str) -> bool:
    return _norm(tag) == "select"
