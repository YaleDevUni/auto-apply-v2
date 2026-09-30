"""페이지 snapshot 과 업로드 파일 — PageDriver port 가 주고받는 모양 (§A5).

snapshot 은 에이전트(LLM)에게 그대로 간다. 비밀번호·인증 코드 칸의 값은 어댑터가 읽지 않고,
어댑터가 실수로 실어도 여기서 한 번 더 버린다(절대 규칙 3).
"""

from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator

from auto_apply.contracts._base import Frozen
from auto_apply.domain.page_elements import is_secret_field

# agent-browser 스타일 ref. 드라이버가 snapshot 마다 새 번호를 매긴다 — 이전 snapshot 의 ref 는
# 재사용되지 않아 엉뚱한 요소를 가리킬 수 없다.
REF_PATTERN = r"^e[1-9][0-9]{0,6}$"
Ref = Annotated[str, StringConstraints(pattern=REF_PATTERN)]


class SnapshotNode(Frozen):
    """접근성 트리의 한 줄. 조작할 수 있는 요소만 `ref` 를 갖고, 제목·본문은 맥락용이다."""

    ref: Ref | None = None
    role: str  # ARIA role (명시·암묵). 파일 입력은 "file", 본문은 "text"
    name: str = ""  # 접근 이름 (라벨·aria-label·텍스트)
    tag: str = ""  # 소문자 태그
    input_type: str | None = None  # input 의 type 속성 원문
    autocomplete: str | None = None
    value: str | None = None  # 현재 값 (텍스트 칸·select 의 선택 라벨)
    checked: bool | None = None
    options: tuple[str, ...] = ()  # select 의 선택지 라벨
    disabled: bool = False
    required: bool = False
    secret: bool = False  # 비밀번호·인증 코드 칸 — value 는 늘 None
    hidden: bool = False  # 화면에 안 보이지만 조작할 수 있는 요소 (숨긴 파일 입력)
    level: int | None = None  # heading 수준
    frame: int = Field(default=0, ge=0)  # PageSnapshot.frames 의 번호 (0 = 최상위 문서)

    @model_validator(mode="before")
    @classmethod
    def _never_carry_secret_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        secret = bool(data.get("secret")) or is_secret_field(
            str(data.get("tag") or ""), data.get("input_type"), data.get("autocomplete")
        )
        return {**data, "secret": True, "value": None} if secret else data


class PageSnapshot(Frozen):
    url: str
    title: str = ""
    frames: tuple[str, ...] = ()  # 프레임 URL, 번호 = SnapshotNode.frame
    nodes: tuple[SnapshotNode, ...] = ()
    truncated: bool = False  # 노드 상한에 걸려 뒤가 잘렸다

    def node(self, ref: str) -> SnapshotNode | None:
        return next((n for n in self.nodes if n.ref == ref), None)


class UploadFile(Frozen):
    """앱이 관리하는 문서의 바이트 (§A7) — 사용자 PC 의 임의 경로는 이 모양이 될 수 없다."""

    name: str
    content_type: str
    data: bytes
