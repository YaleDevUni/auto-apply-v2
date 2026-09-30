"""사람 핸드오프 — run 이 사람에게 넘기는 일과 사람의 응답 (§A5 request_login·request_human·
ask_user).

로그인·CAPTCHA·본인인증은 사람이 전용 크롬 창에서 한다(절대 규칙 3). 에이전트는 무엇을 부탁하는지만
적고, 기다리는 동안 도구를 쓰지 못한다. 승인 화면·알림에 그대로 나가는 기록이라 고유식별정보가
있으면 만들어지지 않는다(절대 규칙 5) — 만드는 쪽이 먼저 가린다.
"""

from enum import StrEnum

from pydantic import ConfigDict, Field

from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.domain.human_handoff import HandoffSignal


class HumanTaskKind(StrEnum):
    LOGIN = "login"  # request_login — 끝까지 못 하면 NEEDS_LOGIN
    INPUT = "input"  # request_human(CAPTCHA·본인인증 등) — 끝까지 못 하면 NEEDS_INPUT
    QUESTION = "question"  # ask_user(프로필에 없는 항목, D10) — 답이 안 오면 NEEDS_INPUT


class HumanOutcome(StrEnum):
    DONE = "done"  # 사람이 끝냈다 — run 이 이어간다
    DECLINED = "declined"  # 사람이 하지 않겠다고 했다
    TIMED_OUT = "timed_out"  # 최대 대기를 넘겼다 (게이트가 만든다 — 사람이 줄 수 없다)


class HumanTask(IdentifierFree):
    id: str = Field(min_length=1, max_length=64)
    kind: HumanTaskKind
    application_id: str | None = None
    run_id: str | None = None
    site: str = Field(default="", max_length=200)  # request_login 의 사이트 이름(에이전트가 쓴 글)
    reason: str = Field(default="", max_length=2000)  # request_human 의 이유(에이전트가 쓴 글)
    page_url: str = Field(default="", max_length=2048)  # 넘길 때의 탭 주소
    # ask_user — 무엇을 묻는가(에이전트가 쓴 글). sensitive 면 답을 답변 KB 에 남기지 않는다
    question: str = Field(default="", max_length=500)
    field_hint: str = Field(default="", max_length=200)
    options: tuple[str, ...] = Field(default=(), max_length=50)
    sensitive: bool = False
    # 하네스가 본 근거(로그인 벽·CAPTCHA …) — 에이전트의 말과 별개다
    signal: HandoffSignal | None = None


class HumanReply(Frozen):
    # 답에 고유식별정보가 있을 수 있다(사이트엔 넣되 기록 안 함) — 에러에 입력값을 싣지 않는다
    model_config = ConfigDict(hide_input_in_errors=True)

    outcome: HumanOutcome
    note: str = Field(default="", max_length=500)  # 사람이 남긴 말 — 에이전트에게 간다
    # ask_user 의 답. 로그·저장하지 않는다 — 어디에 남는지는 toolbox 가 정한다(§A5 ask_user)
    answer: str = Field(default="", max_length=20_000, repr=False)
