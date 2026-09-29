from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from auto_apply.domain.unique_identifiers import reject_unique_identifiers


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentifierFree(Frozen):
    """모든 문자열 필드(중첩 포함)에 고유식별정보가 없어야 만들어지는 DTO (절대 규칙 5).

    저장 대상 최상위 DTO(Profile·Experience·Answer·DocumentMeta)만 상속한다 — 중첩 모델은
    최상위가 `model_dump()` 전체를 훑을 때 같이 검사된다. 생성 시점 검사는 `model_copy(update=...)`
    ·`model_construct` 로 건너뛸 수 있어, repository 가 저장 직전에 `ensure_identifier_free` 로
    한 번 더 본다.

    `hide_input_in_errors`: pydantic 은 기본으로 ValidationError 에 입력값을 싣는다 — 거부한
    번호가 에러 메시지·로그·API 응답으로 새지 않게 끈다.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    @model_validator(mode="after")
    def _no_unique_identifiers(self) -> Self:
        ensure_identifier_free(self)
        return self


def ensure_identifier_free(model: BaseModel) -> None:
    """고유식별정보가 있으면 `UniqueIdentifierRejected`. 메시지에는 모델 이름만 싣는다."""
    reject_unique_identifiers(model.model_dump(mode="json"), where=type(model).__name__)
