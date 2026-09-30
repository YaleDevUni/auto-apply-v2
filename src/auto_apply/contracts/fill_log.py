"""FillLog — 에이전트가 무엇을 어디에 무엇을 근거로 넣었는가 (§A5, D8).

승인 화면이 보여 주고, 승인 뒤 재진입 run 이 이 기록으로 다시 입력·대조한다(§A4 L6).
기록은 도구가 성공한 뒤 BrowserToolbox 가 남긴다 — 에이전트가 직접 쓰지 않는다.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from auto_apply.contracts._base import Frozen, IdentifierFree

# profile 필드 경로·fact id·답변 id·문서 id. 경로·공백·따옴표를 받지 않는다.
SOURCE_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$"


class FillSourceKind(StrEnum):
    PROFILE = "profile"  # key = 인적사항 필드 (예: "email", "links.github")
    FACT = "fact"  # key = fact id
    ANSWER_KB = "answer_kb"  # key = 답변 id
    GENERATED = "generated"  # 생성물 (자소서 답변·문서) — key 는 있으면 생성물 id
    USER = "user"  # 실행 중 사람이 답한 값 (ask_user)


_KEY_REQUIRED = frozenset({FillSourceKind.PROFILE, FillSourceKind.FACT, FillSourceKind.ANSWER_KB})


class FillSource(Frozen):
    """입력값의 근거. 근거 없는 입력은 도구가 받지 않는다.

    절대 규칙 4 — 근거가 맞는지는 승인 화면·ground_check 가 본다.
    """

    kind: FillSourceKind
    key: str | None = Field(default=None, pattern=SOURCE_KEY_PATTERN)

    @model_validator(mode="after")
    def _key_when_required(self) -> Self:
        if self.kind in _KEY_REQUIRED and self.key is None:
            raise ValueError(f"source.kind={self.kind.value} 에는 key 가 필요하다")
        return self


class FillAction(StrEnum):
    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    UPLOAD = "upload"


class FieldLabel(Frozen):
    """입력한 칸을 사람이 알아볼 만큼 — snapshot 의 role·접근 이름과 그때의 페이지."""

    role: str
    name: str = ""
    url: str  # 칸이 있던 문서(프레임)의 URL


class FillEntry(IdentifierFree):
    seq: int = Field(ge=1)
    # 몇 번째 승인 단계에서 넣었나 — type=submit 다단계 사이트는 단계마다 승인을 받는다
    # (§A4 L6 확장).
    step: int = Field(default=1, ge=1)
    action: FillAction
    ref: str
    field: FieldLabel
    source: FillSource | None = None  # upload 는 문서 자체가 근거라 없다
    value: str | None = None  # fill 한 값 · select 한 선택지 라벨
    checked: bool | None = None  # check
    document_id: str | None = None  # upload
    # 값에 고유식별정보가 있어 기록하지 않았다 — 승인 뒤 재입력 때 다시 물어야 한다(절대 규칙 5).
    withheld: bool = False

    @model_validator(mode="after")
    def _shape_matches_action(self) -> Self:
        expected = {
            FillAction.FILL: self.value is not None or self.withheld,
            FillAction.SELECT: self.value is not None or self.withheld,
            FillAction.CHECK: self.checked is not None,
            FillAction.UPLOAD: self.document_id is not None,
        }[self.action]
        if not expected or (self.source is None) != (self.action is FillAction.UPLOAD):
            raise ValueError(f"{self.action.value} 기록의 모양이 맞지 않다")
        if self.withheld and self.value is not None:
            raise ValueError("withheld 기록은 값을 싣지 않는다")
        return self


class FillLog(Frozen):
    entries: tuple[FillEntry, ...] = ()

    def append(self, entry: FillEntry) -> "FillLog":
        return FillLog(entries=(*self.entries, entry))

    @property
    def next_seq(self) -> int:
        return len(self.entries) + 1
