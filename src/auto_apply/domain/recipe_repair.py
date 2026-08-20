"""Repair candidate 조립 (§2.4 node B~D 사이). 순수 함수 — LLM이 낸 actions/success_signals 와

이전 recipe 의 나머지 필드(platform/form_hash/expected_elements/validation_rules)를 코드가
합친다. Recipe/이력서와 같은 "AI는 생성만, 판정·조합은 코드" 철학의 연장이다.
"""

from auto_apply.contracts.recipe import Action, AutomationRecipe


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
