"""AutomationRecipe = 코드가 아니라 데이터 (ARCHITECTURE.md §3).

workflow payload 로 오가므로 contracts 에 둔다. 벤더 SDK 를 import 하지 않는다.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_apply.domain.enums import RecipePolicyBlockerCode


class ActionType(StrEnum):
    GOTO = "goto"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    UPLOAD = "upload"
    WAIT_FOR = "wait_for"
    ASSERT_VISIBLE = "assert_visible"
    SCREENSHOT = "screenshot"
    SUBMIT = "submit"  # 특별 취급: 되돌릴 수 없음


SELECTOR_VALUE_PLACEHOLDER = "{value}"
# 이 placeholder 를 selector 에 쓸 수 있는 action — 지원 건마다 달라지는 텍스트(방금 올린
# 이력서 파일명, 카테고리별 포트폴리오 파일명 등)로 매칭 대상을 좁혀야 하는 액션들.
# FILL/SELECT/UPLOAD 는 value_ref/value_literal 을 이미 "채워 넣을 값" 으로 쓰고 있어서
# 여기 포함하지 않는다 — 같은 필드를 selector 치환과 채워넣기 두 용도로 겹쳐 쓰면 헷갈린다.
_SELECTOR_TEMPLATABLE = {ActionType.CLICK, ActionType.WAIT_FOR, ActionType.ASSERT_VISIBLE}


class Action(BaseModel):
    # extra="forbid" = LLM 이 창작한 필드를 실행 계층까지 흘려보내지 않는다
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ActionType
    selector: str | None = None
    value_ref: str | None = Field(
        default=None,
        description=(
            "예: 'profile.email' — 참조만 허용. "
            "CLICK/WAIT_FOR/ASSERT_VISIBLE 에서는 selector 안의 '{value}' 자리에 꽂힌다"
        ),
    )
    value_literal: str | None = None
    timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    optional: bool = False
    # 페이지 경계(§ supervised-checkpoint-design) — SUPERVISED 모드에서 이 액션 실행 전에
    # 스크린샷을 찍어 사람 승인을 기다린다. SUBMIT 은 이 플래그 없이도 항상 체크포인트가
    # 걸린다(CLAUDE.md 절대규칙 4) — recipe 가 깜빡 세우지 않아도 최종 제출은 항상 막힌다.
    checkpoint: bool = False

    @model_validator(mode="after")
    def selector_required_for_dom_actions(self) -> Self:
        needs_selector = {
            ActionType.CLICK,
            ActionType.FILL,
            ActionType.SELECT,
            ActionType.UPLOAD,
            ActionType.WAIT_FOR,
            ActionType.ASSERT_VISIBLE,
        }
        if self.type in needs_selector and not self.selector:
            raise ValueError(f"{self.type} 는 selector 가 필요하다")
        if self.type is ActionType.FILL and not (self.value_ref or self.value_literal):
            raise ValueError("fill 은 value_ref 또는 value_literal 이 필요하다")
        if (
            self.selector
            and SELECTOR_VALUE_PLACEHOLDER in self.selector
            and not (self.value_ref or self.value_literal)
        ):
            raise ValueError(
                f"selector 에 {SELECTOR_VALUE_PLACEHOLDER} 를 쓰려면 "
                "value_ref 또는 value_literal 이 필요하다"
            )
        if (
            self.selector
            and SELECTOR_VALUE_PLACEHOLDER in self.selector
            and self.type not in _SELECTOR_TEMPLATABLE
        ):
            raise ValueError(
                f"{SELECTOR_VALUE_PLACEHOLDER} 는 {sorted(_SELECTOR_TEMPLATABLE)} 에서만 쓸 수 있다"
            )
        return self


class ValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["text_present", "url_matches", "element_count"]
    target: str
    expected: str


class AutomationRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    version: int = Field(ge=1)
    status: Literal["draft", "candidate", "active", "deprecated"]
    form_hash: str
    actions: list[Action] = Field(min_length=1, max_length=120)
    expected_elements: list[str] = Field(default_factory=list)
    success_signals: list[str] = Field(min_length=1)
    validation_rules: list[ValidationRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def submit_must_be_terminal(self) -> Self:
        idx = [i for i, a in enumerate(self.actions) if a.type is ActionType.SUBMIT]
        if len(idx) > 1:
            raise ValueError("submit action 은 최대 1개")
        if idx and idx[0] != len(self.actions) - 1:
            raise ValueError("submit 은 마지막 action 이어야 한다")
        return self

    @property
    def has_submit(self) -> bool:
        return any(a.type is ActionType.SUBMIT for a in self.actions)


class RecipePolicyBlocker(BaseModel):
    """domain/recipe_policy.py::check_recipe_policy() 의 결과 항목 하나."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: RecipePolicyBlockerCode
    detail: str = ""


class RecipePolicyVerdict(BaseModel):
    """domain/recipe_policy.py::check_recipe_policy() 의 결과."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    safe: bool
    blockers: list[RecipePolicyBlocker] = Field(default_factory=list)
