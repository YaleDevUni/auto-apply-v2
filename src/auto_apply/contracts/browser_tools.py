"""BrowserToolbox 도구의 입력·결과 모양 (§A5, §A4 L1).

입력 모델이 곧 에이전트에게 보이는 도구 스키마다(M3 가 JSON Schema 로 노출). LLM 이 만든 인자는
여기서 검증을 통과해야만 브라우저에 닿는다. 임의 JS·키 입력·좌표·CSS 선택자·파일 경로를 받는
필드는 **없다** — 요소는 snapshot 의 ref 로만, 파일은 앱이 관리하는 문서 id 로만 가리킨다.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from auto_apply.contracts._base import Frozen
from auto_apply.contracts.fill_log import FillSource, FillSourceKind
from auto_apply.contracts.page import PageSnapshot, Ref
from auto_apply.contracts.submit_guard import GuardReport
from auto_apply.domain.human_handoff import HandoffSignal

# 긴 자소서 답변(수천 자)도 한 번에 넣을 수 있게, 그러나 페이지를 망가뜨릴 만큼은 아니게.
MAX_FILL_CHARS = 20_000
MAX_WAIT_MS = 10_000
DocumentId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class ToolInput(Frozen):
    # LLM 이 준 인자를 에러 메시지로 되돌려 보내지 않는다 — 고유식별정보가 섞였을 수 있다
    # (절대 규칙 5).
    model_config = ConfigDict(hide_input_in_errors=True)


class SnapshotInput(ToolInput):
    pass


class NavigateInput(ToolInput):
    url: str = Field(max_length=2048)

    @field_validator("url")
    @classmethod
    def _web_url_only(cls, v: str) -> str:
        # javascript:·data:·file:·chrome: 이동은 임의 JS 실행·로컬 파일 열람 통로다 (L1).
        parts = urlsplit(v.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("http(s) 주소만 열 수 있다")
        return v.strip()


class BackInput(ToolInput):
    pass


class ScrollInput(ToolInput):
    ref: Ref | None = None  # 있으면 그 요소가 보이게, 없으면 한 화면
    direction: Literal["down", "up"] = "down"


class WaitForInput(ToolInput):
    text: str | None = Field(default=None, min_length=1, max_length=200)
    ms: int | None = Field(default=None, ge=1, le=MAX_WAIT_MS)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.text is None) == (self.ms is None):
            raise ValueError("text 와 ms 중 하나만 준다")
        return self


class FillInput(ToolInput):
    ref: Ref
    value: str = Field(max_length=MAX_FILL_CHARS)
    source: FillSource


class SelectInput(ToolInput):
    ref: Ref
    # 선택지 라벨(없으면 value 로도 찾는다). 비워 두는 것은 가린 답(ask_user sensitive)을 고를 때뿐
    option: str = Field(max_length=500)
    source: FillSource

    @model_validator(mode="after")
    def _option_or_hidden_answer(self) -> Self:
        if not self.option and self.source.kind is not FillSourceKind.USER:
            raise ValueError("option 이 비었다")
        return self


class CheckInput(ToolInput):
    ref: Ref
    on: bool
    source: FillSource


class UploadInput(ToolInput):
    ref: Ref
    document_id: DocumentId


class ReportFailureInput(ToolInput):
    reason: str = Field(min_length=1, max_length=2000)


class ClickInput(ToolInput):
    # ref 하나뿐이다 — 모드·강제·좌표 같은 인자로 하네스를 바꿀 통로가 없다 (§A4).
    ref: Ref


class ReadyForReviewInput(ToolInput):
    submit_ref: Ref  # 사람이 승인하면 하네스가 누를 요소 (에이전트는 누르지 않는다)
    notes: str = Field(default="", max_length=2000)


class RequestLoginInput(ToolInput):
    site: str = Field(min_length=1, max_length=200)  # 사람에게 보일 사이트 이름


class RequestHumanInput(ToolInput):
    reason: str = Field(min_length=1, max_length=2000)  # 사람에게 부탁할 일 (CAPTCHA·본인인증 …)


class AskUserInput(ToolInput):
    question: str = Field(min_length=1, max_length=500)  # 사람에게 보일 질문 (답변 KB 의 키)
    field_hint: str = Field(default="", max_length=200)  # 어느 칸인가 (라벨·형식)
    options: tuple[Annotated[str, StringConstraints(min_length=1, max_length=200)], ...] | None = (
        Field(default=None, min_length=1, max_length=50)
    )
    # 민감한 답(건강·가족 등) — 답변 KB 에 저장하지 않고 에이전트에게도 값을 보이지 않는다
    sensitive: bool = False


class ToolError(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNKNOWN_TOOL = "unknown_tool"
    RUN_FINISHED = "run_finished"  # report_failure 뒤에는 어떤 도구도 받지 않는다
    FORBIDDEN_URL = "forbidden_url"  # 앱 자신(승인 콘솔) 같은 금지 출처
    DOCUMENT_NOT_FOUND = "document_not_found"
    STALE_REF = "stale_ref"
    UNSUPPORTED_ELEMENT = "unsupported_element"
    SECRET_FIELD = "secret_field"
    OPTION_NOT_FOUND = "option_not_found"
    NAVIGATION_FAILED = "navigation_failed"
    TIMEOUT = "timeout"
    # §A4 — 이 동작 창에서 하네스가 제출로 보이는 요청·폼 제출을 막았다. 명확한 단계 이동은
    # 통과하므로(D17) 최종 제출이거나 애매한 버튼이다: 입력을 마쳤으면 ready_for_review 로
    # 승인을 받는다.
    SUBMIT_BLOCKED = "submit_blocked"
    INCIDENT = "incident"  # L5 — 제출이 뚫린 흔적. run 은 멈췄고 어떤 도구도 받지 않는다
    GUARD_UNAVAILABLE = "guard_unavailable"  # 하네스를 못 켰다 — 동작하지 않았다
    CAPTCHA = "captcha"  # CAPTCHA 위젯·답 칸 — 사람 몫이다(절대 규칙 3), request_human
    # 사람이 로그인·확인하는 동안(가드가 꺼져 있다) 에이전트 도구는 받지 않는다 (§A5)
    AWAITING_HUMAN = "awaiting_human"
    NEEDS_LOGIN = "needs_login"  # request_login 을 사람이 끝내지 않았다(타임아웃·거절) — run 끝
    NEEDS_INPUT = "needs_input"  # request_human 을 사람이 끝내지 않았다 — run 끝
    RUN_LIMIT = "run_limit"  # run 의 도구 호출 수·시간 상한을 넘었다 — run 끝 (§A6)
    # ask_user 에 사람이 답하지 않겠다고 했다 — run 은 계속된다(그 칸 없이 가거나 report_failure)
    ANSWER_DECLINED = "answer_declined"


class UserAnswer(Frozen):
    """ask_user 의 답. `source` 를 fill·select 에 그대로 쓴다.

    `value` 가 None 이면 가린 답이다(sensitive·고유식별정보) — 값은 앱만 쥐고, 그 source 로 부르면
    앱이 채운다(fill value 는 비워 둔다). 에이전트·기록·transcript 에 값이 남지 않는다(절대 규칙 5).
    """

    source: FillSource
    value: str | None = None


class ToolResult(Frozen):
    """도구 한 번의 결과. 실패도 예외가 아니라 결과다 — 에이전트가 읽고 다른 방법을 고른다."""

    tool: str
    ok: bool
    error: ToolError | None = None
    message: str = ""
    snapshot: PageSnapshot | None = None
    found: bool | None = None  # wait_for(text)
    # snapshot: 이 화면이 사람 몫으로 보이는 근거(로그인 벽·CAPTCHA·인증 코드) — 하네스가 판단한다
    handoff: HandoffSignal | None = None
    answer: UserAnswer | None = None  # ask_user
    # 직전 결과 뒤로 하네스가 막은 것·처리한 대화상자 (창 사이에 사이트가 스스로 보낸 것 포함)
    guard: GuardReport = GuardReport()
