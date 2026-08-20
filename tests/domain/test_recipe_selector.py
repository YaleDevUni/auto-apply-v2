"""resolve_selector — Action.selector 안의 '{value}' 치환 (§3)."""

import pytest

from auto_apply.contracts.recipe import Action, ActionType
from auto_apply.domain.recipe_selector import resolve_selector


def test_selector_without_placeholder_is_unchanged():
    action = Action(type=ActionType.CLICK, selector="#submit")
    assert resolve_selector(action, {}) == "#submit"


def test_placeholder_resolved_from_profile():
    action = Action(
        type=ActionType.CLICK,
        selector='li:has-text("{value}") input[type="checkbox"]',
        value_ref="profile.resume_filename",
    )
    result = resolve_selector(action, {"resume_filename": "박예일_이력서.pdf"})
    assert result == 'li:has-text("박예일_이력서.pdf") input[type="checkbox"]'


def test_placeholder_resolved_from_value_literal():
    action = Action(
        type=ActionType.WAIT_FOR,
        selector='text="{value}"',
        value_literal="첨부파일 선택",
    )
    assert resolve_selector(action, {}) == 'text="첨부파일 선택"'


def test_missing_profile_key_raises():
    action = Action(
        type=ActionType.CLICK,
        selector='text="{value}"',
        value_ref="profile.resume_filename",
    )
    with pytest.raises(ValueError, match="resume_filename"):
        resolve_selector(action, {})


def test_empty_resolved_value_raises():
    """빈 값을 그대로 꽂으면 :has-text("") 처럼 의도와 다른 selector 가 만들어진다 — 값이 없는

    경우와 구분해서 막는다. 이 selector 를 쓰는 액션은 optional=True 로 짜서 건너뛰게 한다."""
    action = Action(
        type=ActionType.CLICK,
        selector='li:has-text("{value}") input[type="checkbox"]',
        value_ref="profile.portfolio_filename",
    )
    with pytest.raises(ValueError, match="비어"):
        resolve_selector(action, {"portfolio_filename": ""})


def test_value_containing_quote_is_escaped():
    action = Action(
        type=ActionType.CLICK,
        selector='li:has-text("{value}")',
        value_literal='weird"file.pdf',
    )
    assert resolve_selector(action, {}) == 'li:has-text("weird\\"file.pdf")'
