"""classify_selector — AgentBrowserExecutor 가 넘겨받는 selector 문자열 분류 (§3)."""

from auto_apply.domain.agent_browser_selector import (
    FindLocator,
    TextFilterTarget,
    classify_selector,
)


def test_plain_css_passes_through():
    assert classify_selector('input[type="file"]') == 'input[type="file"]'


def test_plain_css_with_attribute_passes_through():
    assert classify_selector('button[role="radio"][value="RESUME"]') == (
        'button[role="radio"][value="RESUME"]'
    )


def test_role_engine_without_name():
    assert classify_selector("role=button") == FindLocator(locator="role", value="button")


def test_role_engine_with_name():
    assert classify_selector('role=button[name="완료"]') == FindLocator(
        locator="role", value="button", name="완료"
    )


def test_text_engine_bare_is_substring():
    assert classify_selector("text=첨부파일 선택") == FindLocator(
        locator="text", value="첨부파일 선택"
    )


def test_text_engine_quoted_is_exact():
    assert classify_selector('text="첨부파일 선택"') == FindLocator(
        locator="text", value="첨부파일 선택", exact=True
    )


def test_other_engine_prefixes():
    assert classify_selector("label=Email") == FindLocator(locator="label", value="Email")
    assert classify_selector("testid=submit-btn") == FindLocator(
        locator="testid", value="submit-btn"
    )


def test_has_text_without_descendant():
    result = classify_selector('button:has-text("제출하기")')
    assert result == TextFilterTarget(base_selector="button", exact=False, text="제출하기")


def test_text_is_without_descendant():
    result = classify_selector('button:text-is("제출하기")')
    assert result == TextFilterTarget(base_selector="button", exact=True, text="제출하기")


def test_has_text_with_descendant_selector():
    result = classify_selector('li:has-text("박예일_이력서.pdf") label')
    assert result == TextFilterTarget(
        base_selector="li", exact=False, text="박예일_이력서.pdf", descendant_selector="label"
    )


def test_has_text_with_no_base_defaults_to_wildcard():
    result = classify_selector(':has-text("hello")')
    assert result.base_selector == "*"


def test_has_text_value_with_escaped_quote():
    result = classify_selector(r'li:has-text("weird\"file.pdf")')
    assert isinstance(result, TextFilterTarget)
    assert result.text == 'weird"file.pdf'
