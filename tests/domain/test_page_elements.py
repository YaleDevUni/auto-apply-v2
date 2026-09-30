"""도구가 요소에 할 수 있는 일(§A5)·열 수 없는 주소 — 순수 규칙."""

import pytest

from auto_apply.domain import page_elements as rules
from auto_apply.domain.url_policy import is_forbidden_url


@pytest.mark.parametrize(
    ("tag", "type_", "autocomplete"),
    [
        ("input", "password", None),
        ("INPUT", " Password ", None),
        ("input", "text", "current-password"),  # "비밀번호 보기" 토글로 type=text 가 된 칸
        ("input", None, "new-password"),
        ("input", "tel", "one-time-code"),  # SMS 인증 코드 (절대 규칙 3)
        ("input", "text", "section-login ONE-TIME-CODE"),
    ],
)
def test_secret_fields(tag, type_, autocomplete):
    assert rules.is_secret_field(tag, type_, autocomplete)


@pytest.mark.parametrize(
    ("tag", "type_", "autocomplete"),
    [
        ("input", "text", "username"),
        ("input", "email", "email"),
        ("textarea", "password", None),  # textarea 에는 type 이 없다
        ("select", None, None),
    ],
)
def test_not_secret(tag, type_, autocomplete):
    assert not rules.is_secret_field(tag, type_, autocomplete)


@pytest.mark.parametrize(
    ("tag", "type_", "expected"),
    [
        ("input", None, True),
        ("input", "", True),
        ("input", "EMAIL", True),
        ("input", "made-up", True),  # HTML: 모르는 type = text
        ("input", "date", True),
        ("textarea", None, True),
        ("input", "password", False),
        ("input", "checkbox", False),
        ("input", "file", False),
        ("input", "submit", False),
        ("input", "image", False),
        ("input", "hidden", False),
        ("button", None, False),
        ("select", None, False),
        ("div", None, False),
    ],
)
def test_accepts_text(tag, type_, expected):
    assert rules.accepts_text(tag, type_) is expected


def test_contenteditable_accepts_text():
    assert rules.accepts_text("div", None, contenteditable=True)


def test_toggles_files_selects():
    assert rules.is_toggle("input", "checkbox") and rules.is_toggle("input", "Radio")
    assert not rules.is_toggle("div", None) and not rules.is_toggle("input", "button")
    assert rules.is_radio("input", "radio") and not rules.is_radio("input", "checkbox")
    assert rules.is_file_input("input", "FILE") and not rules.is_file_input("button", "file")
    assert rules.is_native_select("SELECT") and not rules.is_native_select("div")


APP = ["http://127.0.0.1:8000"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/api/applications/1/approve",
        "http://localhost:8000/",
        "http://app.localhost:8000/",
        "http://127.1.2.3:8000/",
        "http://[::1]:8000/",
        "https://127.0.0.1:8000/",
        "http://localhost.:8000/",
        "http://127.0.0.1:99999/",  # 해석 못 하는 주소는 금지
        "not a url",
    ],
)
def test_forbidden(url):
    assert is_forbidden_url(url, APP)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8001/", "http://127.0.0.1/", "https://example.com:8000/", "https://x.dev/"],
)
def test_allowed(url):
    assert not is_forbidden_url(url, APP)


def test_nothing_forbidden_by_default():
    assert not is_forbidden_url("http://127.0.0.1:8000/", [])
