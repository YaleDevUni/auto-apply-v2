"""build_candidate_recipe() — §2.4 node B~D 사이 조합 단계 회귀 테스트."""

from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe, ValidationRule
from auto_apply.domain.recipe_repair import build_candidate_recipe


def _previous() -> AutomationRecipe:
    return AutomationRecipe(
        platform="wanted",
        version=3,
        status="active",
        form_hash="h-wanted-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1"),
            Action(type=ActionType.FILL, selector="#email", value_ref="profile.email"),
            Action(type=ActionType.SUBMIT, selector="#submit"),
        ],
        expected_elements=["#form"],
        success_signals=["지원이 완료되었습니다"],
        validation_rules=[ValidationRule(kind="text_present", target="body", expected="완료")],
    )


def test_candidate_is_draft_regardless_of_previous_status():
    """AI 가 만든 recipe 는 절대 active 로 바로 안 올라간다 — 항상 draft 로 시작한다."""
    previous = _previous()
    candidate = build_candidate_recipe(
        previous,
        actions=[Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1")],
        success_signals=["완료"],
        version=4,
    )
    assert candidate.status == "draft"
    assert candidate.version == 4


def test_candidate_inherits_identity_fields_from_previous():
    """platform/form_hash/expected_elements/validation_rules 는 LLM 이 건드리지 않는다 —

    폼 자체의 정체성이라 코드가 이전 recipe 값을 그대로 물려준다.
    """
    previous = _previous()
    new_actions = [Action(type=ActionType.CLICK, selector="#new-button")]
    candidate = build_candidate_recipe(
        previous, actions=new_actions, success_signals=["새 성공 신호"], version=4
    )
    assert candidate.platform == previous.platform
    assert candidate.form_hash == previous.form_hash
    assert candidate.expected_elements == previous.expected_elements
    assert candidate.validation_rules == previous.validation_rules
    assert candidate.actions == new_actions
    assert candidate.success_signals == ["새 성공 신호"]
