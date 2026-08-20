"""check_recipe_policy() — §3 정책 체크리스트 회귀 테스트."""

from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import RecipePolicyBlockerCode
from auto_apply.domain.recipe_policy import check_recipe_policy


def _recipe(
    *, version: int = 1, status: str = "candidate", actions: list[Action]
) -> AutomationRecipe:
    return AutomationRecipe(
        platform="fixture",
        version=version,
        status=status,  # type: ignore[arg-type]
        form_hash="h-fixture",
        actions=actions,
        success_signals=["완료"],
    )


def _clean_recipe(**kw) -> AutomationRecipe:
    return _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1"),
            Action(type=ActionType.FILL, selector="#email", value_ref="profile.email"),
            Action(type=ActionType.SUBMIT, selector="#submit"),
        ],
        **kw,
    )


def test_clean_recipe_has_no_blockers():
    verdict = check_recipe_policy(_clean_recipe())
    assert verdict.safe
    assert verdict.blockers == []


def test_password_like_selector_is_blocked():
    recipe = _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1"),
            Action(type=ActionType.FILL, selector="#password", value_literal="hunter2"),
        ]
    )
    verdict = check_recipe_policy(recipe)
    assert not verdict.safe
    assert verdict.blockers[0].code == RecipePolicyBlockerCode.CREDENTIAL_FIELD


def test_domain_drift_is_blocked_when_previous_exists():
    previous = _clean_recipe(status="active")
    drifted = _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://evil.example.com/apply/1"),
            Action(type=ActionType.SUBMIT, selector="#submit"),
        ]
    )
    verdict = check_recipe_policy(drifted, previous=previous)
    assert not verdict.safe
    assert any(b.code == RecipePolicyBlockerCode.DOMAIN_DRIFT for b in verdict.blockers)


def test_domain_drift_is_skipped_without_previous():
    """비교 대상(previous)이 없으면 도메인 체크 자체를 건너뛴다 — 첫 recipe 는 기준선이 없다."""
    drifted = _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://anywhere.example.com/apply/1"),
        ]
    )
    verdict = check_recipe_policy(drifted)
    assert verdict.safe


def test_submit_selector_change_is_flagged():
    previous = _clean_recipe(status="active")
    changed = _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1"),
            Action(type=ActionType.SUBMIT, selector="#new-submit"),
        ]
    )
    verdict = check_recipe_policy(changed, previous=previous)
    assert not verdict.safe
    assert any(b.code == RecipePolicyBlockerCode.SUBMIT_SELECTOR_CHANGED for b in verdict.blockers)


def test_same_submit_selector_is_not_flagged():
    previous = _clean_recipe(status="active")
    same = _clean_recipe(version=2)
    verdict = check_recipe_policy(same, previous=previous)
    assert verdict.safe


def test_multiple_blockers_all_reported():
    previous = _clean_recipe(status="active")
    bad = _recipe(
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://evil.example.com/apply/1"),
            Action(type=ActionType.FILL, selector="#password", value_literal="hunter2"),
            Action(type=ActionType.SUBMIT, selector="#new-submit"),
        ]
    )
    verdict = check_recipe_policy(bad, previous=previous)
    codes = {b.code for b in verdict.blockers}
    assert codes == {
        RecipePolicyBlockerCode.CREDENTIAL_FIELD,
        RecipePolicyBlockerCode.DOMAIN_DRIFT,
        RecipePolicyBlockerCode.SUBMIT_SELECTOR_CHANGED,
    }
