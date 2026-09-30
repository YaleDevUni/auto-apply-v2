"""SubmitGuard 가 드라이버와 주고받는 모양 (§A4 L3·L4·L5, §A5 ready_for_review).

막힌 요청은 출처(scheme://host:port)만 싣는다 — 경로·쿼리에 입력값·주민번호가 실릴 수
있어서다(절대 규칙 5).
"""

from pydantic import Field

from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.contracts.click import ClickVerdict, ElementDescriptor
from auto_apply.contracts.fill_log import FillLog
from auto_apply.domain.submit_guard_policy import BlockReason


class BlockedAction(Frozen):
    reason: BlockReason
    method: str = ""  # 스크립트 층(form_submit)은 비어 있다
    resource: str = ""  # document·xhr·fetch·ping … 또는 스크립트 층의 동작 이름
    origin: str = ""


class DialogEvent(Frozen):
    kind: str  # alert·confirm·prompt·beforeunload
    message: str = Field(default="", max_length=200)
    accepted: bool


class GuardReport(Frozen):
    """한 구간(창 또는 창 사이)에 하네스가 막은 것과 처리한 대화상자."""

    blocked: tuple[BlockedAction, ...] = ()
    dialogs: tuple[DialogEvent, ...] = ()

    def __add__(self, other: "GuardReport") -> "GuardReport":
        return GuardReport(
            blocked=(*self.blocked, *other.blocked), dialogs=(*self.dialogs, *other.dialogs)
        )


class PageText(Frozen):
    """L5 사후 감지용 — 모든 프레임의 URL 과 보이는 글자 줄."""

    urls: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()


class TargetBox(Frozen):
    """문서 좌표(스크롤 포함) — 사람이 승인 화면에서 위치를 알아보는 용도. 클릭에 쓰지 않는다."""

    x: float
    y: float
    width: float
    height: float


class SubmitTarget(Frozen):
    """ready_for_review 가 확정하는 제출 대상 (§A4 L6 이 승인 뒤 같은 요소인지 대조한다)."""

    element: ElementDescriptor  # 가장 안쪽 요소
    ancestors: tuple[ElementDescriptor, ...] = ()  # 클릭이 닿는 조작 가능한 조상 (버튼·링크 …)
    selectors: tuple[str, ...] = ()  # 선택자 후보 (#id · tag[name] · 경로) — 앞일수록 강하다
    frame_url: str
    page_url: str
    frame_index: int = Field(default=0, ge=0)  # 0 = 최상위 문서
    box: TargetBox | None = None


class ReviewRecord(IdentifierFree):
    """FILL 종료 기록 — 승인 화면이 보여 주고 SUBMITTING run 이 이어받는다.

    저장될 기록이라 고유식별정보가 있으면 만들어지지 않는다 — 만드는 쪽이 먼저 가린다(절대 규칙 5).
    """

    target: SubmitTarget
    verdict: ClickVerdict  # 제출 대상의 L2 판정 (Safe 여도 기록한다 — 판단은 사람 몫)
    notes: str = ""
    step: int = Field(ge=1)  # 다단계 사이트의 몇 번째 승인 단계인가 (§A4 L6 확장)
    fill_log: FillLog
