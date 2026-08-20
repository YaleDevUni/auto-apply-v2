"""Recipe 정책 검증. §3 체크리스트를 코드화한 순수 함수(§2.4 node D — 스키마 검증 다음 단계).

AutomationRecipe 자체의 model_validator(submit_must_be_terminal)는 "구조가 맞는가"(node C)만
본다. 여기서는 "이 recipe 를 신뢰해도 되는가"를 본다 — job_applicability.py 와 같은 스타일로
데이터만 받아 verdict 를 돌려주고, 파일/DB/LLM 을 모른다.
"""

import re
from urllib.parse import urlparse

from auto_apply.contracts.recipe import (
    ActionType,
    AutomationRecipe,
    RecipePolicyBlocker,
    RecipePolicyVerdict,
)
from auto_apply.domain.enums import RecipePolicyBlockerCode

_PASSWORD_LIKE = re.compile(r"password|passwd|pwd", re.IGNORECASE)


def _block(code: RecipePolicyBlockerCode, detail: str) -> RecipePolicyBlocker:
    return RecipePolicyBlocker(code=code, detail=detail)


def _goto_hosts(recipe: AutomationRecipe) -> set[str]:
    """goto action 중 value_literal(정적 URL)이 있는 것만 — value_ref 는 런타임 값이라
    domain 레이어에서는 어느 도메인으로 갈지 알 수 없다(profile 을 안 본다)."""
    hosts = set()
    for action in recipe.actions:
        if action.type is ActionType.GOTO and action.value_literal:
            host = urlparse(action.value_literal).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def _submit_selector(recipe: AutomationRecipe) -> str | None:
    for action in recipe.actions:
        if action.type is ActionType.SUBMIT:
            return action.selector
    return None


def check_recipe_policy(
    candidate: AutomationRecipe, *, previous: AutomationRecipe | None = None
) -> RecipePolicyVerdict:
    """candidate 를 승격 대상으로 신뢰해도 되는지 판정한다.

    previous(같은 platform 의 직전 recipe)가 없으면 비교 기반 체크(도메인/submit 셀렉터)는
    건너뛴다 — 첫 recipe 는 비교 대상이 없다.
    """
    blockers = []

    for action in candidate.actions:
        if action.selector and _PASSWORD_LIKE.search(action.selector):
            blockers.append(
                _block(
                    RecipePolicyBlockerCode.CREDENTIAL_FIELD,
                    f"{action.type}: selector={action.selector!r} 가 비밀번호 필드로 보인다",
                )
            )

    if previous is not None:
        allowed_hosts = _goto_hosts(previous)
        if allowed_hosts:
            drifted = _goto_hosts(candidate) - allowed_hosts
            if drifted:
                blockers.append(
                    _block(
                        RecipePolicyBlockerCode.DOMAIN_DRIFT,
                        f"이전 recipe 에 없던 도메인: {sorted(drifted)}",
                    )
                )

        old_submit, new_submit = _submit_selector(previous), _submit_selector(candidate)
        if candidate.has_submit and previous.has_submit and old_submit != new_submit:
            blockers.append(
                _block(
                    RecipePolicyBlockerCode.SUBMIT_SELECTOR_CHANGED,
                    f"submit selector {old_submit!r} -> {new_submit!r}",
                )
            )

    return RecipePolicyVerdict(safe=not blockers, blockers=blockers)
