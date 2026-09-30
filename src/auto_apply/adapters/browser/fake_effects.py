"""메모리 DOM 요소의 이벤트 핸들러 흉내 — 클릭·변경 때 페이지 스크립트가 하는 일 (§A4 테스트 대역).

실제 사이트의 onclick/onchange 를 데이터로 적는다. `FakeGuardedPageDriver` 가 하네스 규칙
(domain/submit_guard_policy)을 거쳐 실행하고, 통과한 요청만 `sent` 에 남긴다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Send:
    """페이지 스크립트의 요청. `navigation=True` 면 문서 탐색(location.href·링크·폼 GET)."""

    method: str
    url: str
    navigation: bool = False


@dataclass(frozen=True)
class SubmitForm:
    """submit 이벤트·form.submit()·requestSubmit().

    가드가 켜져 있으면 스크립트 층이 먼저 막는다.
    """

    method: str
    url: str


@dataclass(frozen=True)
class Dialog:
    """confirm()/alert()/prompt() — 수락됐을 때만 `then` 을 실행한다."""

    kind: str
    message: str = ""
    then: tuple["Effect", ...] = ()


@dataclass(frozen=True)
class ChooseFile:
    """OS 파일 선택 창을 여는 동작(숨은 파일 입력의 click() 등)."""


@dataclass(frozen=True)
class Show:
    """글자가 화면에 나타난다(완료 문구·오류 문구)."""

    text: str
    role: str = "status"


@dataclass(frozen=True)
class Later:
    """핸들러가 setTimeout 으로 미룬 일 — 동작 창이 끝난 뒤(다음 도구 호출 전) 실행된다."""

    effects: tuple["Effect", ...]


Effect = Send | SubmitForm | Dialog | ChooseFile | Show | Later
