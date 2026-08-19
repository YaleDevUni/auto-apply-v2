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


def test_value_containing_quote_is_escaped():
    action = Action(
        type=ActionType.CLICK,
        selector='li:has-text("{value}")',
        value_literal='weird"file.pdf',
    )
    assert resolve_selector(action, {}) == 'li:has-text("weird\\"file.pdf")'
