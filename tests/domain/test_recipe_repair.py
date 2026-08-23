"""build_candidate_recipe()/classify_failure()/bump_goto_timeout() — §2.4 node B~D 사이

조합 단계 회귀 테스트.
"""

from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe, ValidationRule
from auto_apply.domain.recipe_repair import (
    FailureCategory,
    build_candidate_recipe,
    bump_goto_timeout,
    classify_failure,
)


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


def test_classify_failure_detects_playwright_timeout_signature():
    """실측 사고(wanted-goto-timeout-misdiagnosed-as-recipe-bug) 재발 방지 — Playwright

    TimeoutError 시그니처("Timeout Nms exceeded")가 있으면 selector 문제가 아니라 timeout 으로
    분류해야 repair 가 selector 를 코스메틱하게 바꾸는 오진을 피한다.
    """
    reason = "RecipeExecutionError: goto 실패 (): Page.goto: Timeout 5000ms exceeded."
    assert classify_failure(reason) is FailureCategory.TIMEOUT


def test_classify_failure_detects_timeout_regardless_of_action():
    reason = "RecipeExecutionError: click 실패 (#submit): Locator.click: Timeout 5000ms exceeded"
    assert classify_failure(reason) is FailureCategory.TIMEOUT


def test_classify_failure_falls_back_to_selector_for_non_timeout_reason():
    reason = "RecipeExecutionError: fill 실패 (#email-old): no such element"
    assert classify_failure(reason) is FailureCategory.SELECTOR


def test_classify_failure_unknown_for_empty_reason():
    assert classify_failure("") is FailureCategory.UNKNOWN


def test_bump_goto_timeout_doubles_and_caps_at_10s():
    """재발 방지(wanted-goto-timeout-misdiagnosed-as-recipe-bug) 사고의 실제 값(5000→

    필요했던 건 20000 이었다)과는 별개로, 자동 추측은 사용자가 정한 10초 상한을 넘지 않는다 —
    그 이상 필요하면 "그냥 시간이 모자란" 게 아니라 별도 근본 문제로 보고 LLM/사람에게 넘긴다.
    """
    previous = _previous()  # actions[0] 이 goto, timeout_ms 기본값 5000
    candidate = bump_goto_timeout(previous, 0, version=4)
    assert candidate is not None
    assert candidate.actions[0].timeout_ms == 10_000
    assert candidate.status == "draft"
    assert candidate.version == 4
    # goto 가 아닌 다른 action 들은 손대지 않는다.
    assert candidate.actions[1] == previous.actions[1]
    assert candidate.actions[2] == previous.actions[2]


def test_bump_goto_timeout_returns_none_once_already_at_cap():
    previous = _previous()
    at_cap = previous.model_copy(
        update={
            "actions": [
                previous.actions[0].model_copy(update={"timeout_ms": 10_000}),
                *previous.actions[1:],
            ]
        }
    )
    assert bump_goto_timeout(at_cap, 0, version=4) is None


def test_bump_goto_timeout_returns_none_for_non_goto_action():
    previous = _previous()
    assert bump_goto_timeout(previous, 1, version=4) is None  # actions[1] 은 FILL


def test_bump_goto_timeout_returns_none_for_out_of_range_index():
    previous = _previous()
    assert bump_goto_timeout(previous, 99, version=4) is None
