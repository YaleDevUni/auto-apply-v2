"""클릭 대상 기술자·페이지 관찰과 분류 결과 (§A4 L2, D17).

기술자·관찰은 브라우저 어댑터가 DOM 에서 읽어 채우고(T2.5), 분류는 `domain/submit_classifier.py` 의
순수 함수가 한다. 에이전트(LLM)는 기술자를 만들지 않는다 — ref 만 넘기고 하네스가 DOM 에서 읽는다.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from auto_apply.contracts._base import Frozen


class ElementDescriptor(Frozen):
    """분류에 필요한 만큼의 클릭 대상 기술. 값은 DOM 에서 읽은 그대로 — 정규화는 분류기 몫."""

    tag: str  # 소문자 태그 이름 (button, input, a, div …)
    type: str | None = None  # type 속성 원문. 없으면 None — button 의 기본값 해석은 분류기가 한다
    role: str | None = None  # 명시·암묵 ARIA role
    name: str = ""  # 접근 이름 (aria-label·value·title 등에서 계산된 것)
    text: str = ""  # 보이는 텍스트
    in_form: bool = False  # 폼 소유자가 있다 (form 속성으로 연결된 경우 포함)
    is_form_default_button: bool = False  # 폼의 기본 버튼 (암묵적 제출 대상)
    in_dialog: bool = False  # dialog·role=dialog/alertdialog 안
    href: str | None = None


class PageText(Frozen):
    """모든 프레임을 한 번 훑은 관찰.

    L5 사후 감지(URL·글자 줄)와 D17 마지막 단계 신호(입력칸·진행 표시)에 쓴다.
    """

    urls: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    # 보이는 편집 가능한 입력칸 수 — 텍스트류·textarea·select·radio·contenteditable, 파일 입력은
    # 숨겨져 있어도 센다. 체크박스는 세지 않는다(검토 페이지의 최종 동의 칸은 입력칸이 아니다).
    inputs: int = Field(default=0, ge=0)
    # 보이는 폼 제출 버튼 수 — 입력칸 없는 검토 페이지도 제출 버튼이 남아 있으면 아직 제출 전
    # 화면이다 (단계 이동 사후 확인, D17)
    submitters: int = Field(default=0, ge=0)
    # DOM 진행 표시 (현재 단계, 전체 단계) — `aria-current="step"` 이 목록의 몇 번째인가
    progress: tuple[tuple[int, int], ...] = ()


class ClickRisk(StrEnum):
    SUBMIT_TYPE = "submit_type"  # type=submit/image, 폼 안 type 없는 button
    FORM_DEFAULT_BUTTON = "form_default_button"
    DIALOG_CONFIRM = "dialog_confirm"  # dialog 안의 확인/OK/예
    SUBMIT_WORD = "submit_word"  # 라벨·href 에 제출 어휘
    UNRECOGNIZED = "unrecognized"  # 안전하다고 볼 근거가 없음 — 닫힌 쪽으로 실패
    LAST_STEP = "last_step"  # 단계 어휘지만 같은 페이지에 마지막 단계 신호가 있다 (D17)


class SafeBasis(StrEnum):
    INERT_CONTROL = "inert_control"  # 입력 요소 — 클릭이 제출을 일으킬 수 없다
    NAVIGATION_LINK = "navigation_link"  # 실제 href 가 있는 링크 (GET 이동)
    SAFE_WORD = "safe_word"  # 다음/저장/파일 선택 …


class SafeClick(Frozen):
    kind: Literal["safe"] = "safe"
    basis: SafeBasis
    detail: str = ""  # 근거가 된 어휘 등 (로그용)


class RiskyClick(Frozen):
    kind: Literal["risky"] = "risky"
    reason: ClickRisk
    detail: str = ""


class StepClick(Frozen):
    """단계 이동(D17) — 제출 컨트롤이지만 라벨이 명확한 단계 어휘이고 마지막 단계 신호가 없다.

    STEP 창에서 그 버튼의 폼 제출만 통과시키고, 결과 화면을 하네스가 사후 확인한다.
    """

    kind: Literal["step"] = "step"
    detail: str = ""  # 근거가 된 단계 어휘


ClickVerdict = Annotated[SafeClick | RiskyClick | StepClick, Field(discriminator="kind")]
