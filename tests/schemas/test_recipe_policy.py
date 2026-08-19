"""AutomationRecipe 정책 테스트 — LLM 출력이 실행 계층에 도달하기 전 마지막 방어선 (§3)."""

import pytest
from pydantic import ValidationError

from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe


def _recipe(actions: list[Action], **kw) -> AutomationRecipe:
    return AutomationRecipe(
        platform="wanted",
        version=1,
        status="draft",
        form_hash="h1",
        actions=actions,
        success_signals=["지원이 완료되었습니다"],
        **kw,
    )


def test_submit_must_be_last_action():
    with pytest.raises(ValidationError, match="마지막"):
        _recipe(
            [
                Action(type=ActionType.SUBMIT),
                Action(type=ActionType.CLICK, selector="#x"),
            ]
        )


def test_only_one_submit_allowed():
    with pytest.raises(ValidationError, match="최대 1개"):
        _recipe([Action(type=ActionType.SUBMIT), Action(type=ActionType.SUBMIT)])


def test_unknown_field_is_rejected():
    """LLM 이 창작한 필드는 여기서 막힌다 (extra='forbid')."""
    with pytest.raises(ValidationError):
        Action(type=ActionType.CLICK, selector="#x", javascript="alert(1)")  # type: ignore[call-arg]


def test_dom_action_requires_selector():
    with pytest.raises(ValidationError, match="selector"):
        Action(type=ActionType.CLICK)


def test_fill_requires_a_value_source():
    with pytest.raises(ValidationError, match="value_ref"):
        Action(type=ActionType.FILL, selector="#email")


def test_timeout_upper_bound_prevents_runaway():
    with pytest.raises(ValidationError):
        Action(type=ActionType.WAIT_FOR, selector="#x", timeout_ms=10 * 60 * 1000)


def test_recipe_without_submit_is_valid_dry_run_shape():
    r = _recipe([Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/x")])
    assert r.has_submit is False


def test_selector_placeholder_requires_a_value_source():
    with pytest.raises(ValidationError, match="value_ref"):
        Action(type=ActionType.CLICK, selector='text="{value}"')


def test_selector_placeholder_allowed_for_click():
    Action(type=ActionType.CLICK, selector='text="{value}"', value_literal="포트폴리오")


def test_selector_placeholder_rejected_for_fill():
    """FILL 은 value_ref/value_literal 을 이미 '채울 값' 으로 쓴다 — selector 치환과 겹치면
    안 되므로 CLICK/WAIT_FOR/ASSERT_VISIBLE 로만 제한한다."""
    with pytest.raises(ValidationError, match=r"\{value\}"):
        Action(
            type=ActionType.FILL,
            selector='input:below(:text("{value}"))',
            value_ref="profile.email",
        )
