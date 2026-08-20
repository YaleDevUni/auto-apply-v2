"""RecipeDiffSchema — LLM 출력이 recipe 실행 계층에 닿기 전 마지막 방어선 (§2.4 node C)."""

import pytest
from pydantic import ValidationError

from auto_apply.ai.schemas import RecipeDiffSchema


def test_empty_payload_is_rejected():
    """actions/success_signals 는 필수다 — 빈 payload({}) 는 재프롬프트 대상이다."""
    with pytest.raises(ValidationError):
        RecipeDiffSchema.model_validate({})


def test_actions_reuse_recipe_action_validators():
    """selector 가 필요한 action 인데 없으면 Action 의 model_validator 가 여기서도 걸린다 —

    이 검증이 재프롬프트 루프 안에서 공짜로 실행되는 게 핵심(§2.4 node C).
    """
    with pytest.raises(ValidationError, match="selector"):
        RecipeDiffSchema.model_validate(
            {
                "actions": [{"type": "click"}],
                "success_signals": ["완료"],
            }
        )


def test_valid_diff_round_trips():
    diff = RecipeDiffSchema.model_validate(
        {
            "actions": [
                {"type": "goto", "value_literal": "https://wanted.co.kr/apply/1"},
                {"type": "fill", "selector": "#email-v2", "value_ref": "profile.email"},
                {"type": "submit", "selector": "#submit"},
            ],
            "success_signals": ["지원이 완료되었습니다"],
            "rationale": "#email selector 가 #email-v2 로 바뀌었다",
        }
    )
    assert len(diff.actions) == 3
    assert diff.success_signals == ["지원이 완료되었습니다"]


def test_unknown_field_is_rejected():
    """LLM 이 창작한 필드는 여기서 막힌다 (extra='forbid')."""
    with pytest.raises(ValidationError):
        RecipeDiffSchema.model_validate(
            {
                "actions": [{"type": "goto", "value_literal": "https://wanted.co.kr/apply/1"}],
                "success_signals": ["완료"],
                "notes": "이런 필드는 없다",
            }
        )
