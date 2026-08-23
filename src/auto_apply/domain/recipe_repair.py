"""Repair candidate 조립 (§2.4 node B~D 사이). 순수 함수 — LLM이 낸 actions/success_signals 와

이전 recipe 의 나머지 필드(platform/form_hash/expected_elements/validation_rules)를 코드가
합친다. Recipe/이력서와 같은 "AI는 생성만, 판정·조합은 코드" 철학의 연장이다.
"""

import re
from enum import StrEnum

from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe

# Playwright TimeoutError 메시지 형태: "Page.goto: Timeout 5000ms exceeded." /
# "Locator.click: Timeout 5000ms exceeded waiting for locator(...)" — 실측
# (메모리 wanted-goto-timeout-misdiagnosed-as-recipe-bug).
_TIMEOUT_SIGNATURE = re.compile(r"Timeout \d+ms exceeded")

# `bump_goto_timeout`이 자동으로 올려주는 timeout_ms 상한. 여기까지 올려도 계속 timeout 나면
# "그냥 시간이 모자랐다"가 아니라 사이트가 실제로 안 뜨거나 네트워크가 막혔다는 등 별도의
# 근본 문제일 가능성이 크다는 사용자 판단(2026-08-23 설계 논의) — 그 지점부터는 코드가 계속
# 숫자를 올리는 대신 LLM/사람에게 넘긴다. `Action.timeout_ms`의 절대 상한(60_000, LLM이 수동
# 진단 후 직접 정할 수 있는 값)과는 다른, "자동 추측"에만 적용되는 훨씬 낮은 안전판이다.
_AUTO_TIMEOUT_CAP_MS = 10_000


class FailureCategory(StrEnum):
    TIMEOUT = "timeout"
    SELECTOR = "selector"
    UNKNOWN = "unknown"


def classify_failure(reason: str) -> FailureCategory:
    """`reason`(activity_failure()가 돌려주는 실패 사유 문자열)을 보고 수선 방향을 가른다."""
    if _TIMEOUT_SIGNATURE.search(reason):
        return FailureCategory.TIMEOUT
    return FailureCategory.SELECTOR if reason else FailureCategory.UNKNOWN


def bump_goto_timeout(
    recipe: AutomationRecipe, failed_action_index: int, *, version: int
) -> AutomationRecipe | None:
    """실패한 action 이 `goto`이고 아직 `_AUTO_TIMEOUT_CAP_MS` 아래면, LLM 호출 없이 그

    action 의 timeout_ms 만 결정론적으로 올린 candidate 를 만든다. `goto`는 selector 가 아예
    없는 액션이라 "셀렉터가 틀렸을 가능성"이 원천적으로 없다 — 그래서 LLM 판단(프롬프트 지시)
    없이도 코드가 안전하게 판정할 수 있는 유일한 케이스다(다른 액션의 timeout 은 "셀렉터가
    없어서 못 찾은 것"과 구분이 안 돼 LLM+DOM 판단에 맡긴다, ai/prompts.build_recipe_diff_prompt
    참고). 상한에 이미 도달했으면 None — 그 이상은 자동으로 못 고치는 별도 문제로 보고
    호출자가 평소의 LLM diff 경로로 넘어가게 한다.
    """
    if not (0 <= failed_action_index < len(recipe.actions)):
        return None
    action = recipe.actions[failed_action_index]
    if action.type is not ActionType.GOTO or action.timeout_ms >= _AUTO_TIMEOUT_CAP_MS:
        return None
    new_timeout = min(action.timeout_ms * 2, _AUTO_TIMEOUT_CAP_MS)
    new_actions = list(recipe.actions)
    new_actions[failed_action_index] = action.model_copy(update={"timeout_ms": new_timeout})
    return build_candidate_recipe(
        recipe, actions=new_actions, success_signals=recipe.success_signals, version=version
    )


def build_candidate_recipe(
    previous: AutomationRecipe,
    *,
    actions: list[Action],
    success_signals: list[str],
    version: int,
) -> AutomationRecipe:
    """status 는 항상 "draft"로 시작한다 — 샌드박스 dry-run을 통과해야만

    (`activities/repair.py::save_recipe_candidate` 호출 직전) "candidate"로 바뀐다.
    `expected_elements`/`validation_rules`는 폼 자체의 정체성이라 LLM이 건드리지 않고
    이전 recipe 값을 그대로 물려받는다.
    """
    return AutomationRecipe(
        platform=previous.platform,
        version=version,
        status="draft",
        form_hash=previous.form_hash,
        actions=actions,
        expected_elements=previous.expected_elements,
        success_signals=success_signals,
        validation_rules=previous.validation_rules,
    )
