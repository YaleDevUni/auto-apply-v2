"""selector 안의 '{value}' placeholder 치환 — CLICK/WAIT_FOR/ASSERT_VISIBLE 전용 (§3).

지원 건마다 달라지는 텍스트(방금 올린 이력서 파일명, 카테고리별 포트폴리오 파일명 등)로
매칭 대상을 좁혀야 하는데, Action.selector 는 Recipe 작성 시점에 고정된 문자열이다.
그래서 selector 자체에 '{value}' 를 심어두고 실행 시점에 value_ref/value_literal 로 채운다
(FILL 이 "채울 값" 을 참조하는 것과 같은 관례를 selector 쪽에도 그대로 적용한 것).

executor(Playwright/ReplayExecutor 양쪽)가 이 함수를 그대로 재사용해서 두 구현이
같은 치환 규칙을 갖게 한다 — 계약 하나, 대역 둘 원칙(§11.1).
"""

from auto_apply.contracts.recipe import SELECTOR_VALUE_PLACEHOLDER, Action


def resolve_selector(action: Action, profile: dict[str, str]) -> str:
    """action.selector 의 '{value}' 를 value_ref/value_literal 로 치환한 문자열을 돌려준다.

    placeholder 가 없으면 selector 를 그대로 돌려준다. 값이 CSS 문자열 리터럴
    (예: `:has-text("{value}")`) 안에 들어간다고 가정하고 " 와 \\ 만 이스케이프한다 —
    완전한 CSS 이스케이프는 아니지만, 우리가 selector 에 꽂는 값(파일명 등)은 우리
    파이프라인이 만드므로 이 정도로 충분하다.
    """
    assert action.selector is not None  # Pydantic 검증이 이미 보장한다
    if SELECTOR_VALUE_PLACEHOLDER not in action.selector:
        return action.selector

    if action.value_literal is not None:
        value = action.value_literal
    else:
        assert action.value_ref is not None  # Pydantic 검증이 이미 보장한다
        key = action.value_ref.removeprefix("profile.")
        if key not in profile:
            raise ValueError(f"프로필에 값이 없다: {action.value_ref}")
        value = profile[key]

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return action.selector.replace(SELECTOR_VALUE_PLACEHOLDER, escaped)
