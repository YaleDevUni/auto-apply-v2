"""AutomationRecipe = 코드가 아니라 데이터 (ARCHITECTURE.md §3).

workflow payload 로 오가므로 contracts 에 둔다. 벤더 SDK 를 import 하지 않는다.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class Action(BaseModel):
    # extra="forbid" = LLM 이 창작한 필드를 실행 계층까지 흘려보내지 않는다
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ActionType
    selector: str | None = None
    value_ref: str | None = Field(default=None, description="예: 'profile.email' — 참조만 허용")
    value_literal: str | None = None
    timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    optional: bool = False

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
