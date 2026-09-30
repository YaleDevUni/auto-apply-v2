from dataclasses import dataclass
from typing import Protocol

from auto_apply.contracts.page import PageSnapshot, UploadFile


@dataclass(frozen=True, slots=True)
class PageHandle:
    """브라우저 탭을 가리키는 불투명 핸들 (§A2 벤더 타입 노출 금지).

    구현이 `id` 를 자기 페이지 객체와 짝지어 둔다. 브라우저가 재기동되면 이전 핸들은 아무 탭도
    가리키지 않는다 — 같은 `id` 가 다시 나오지 않는다.
    """

    id: str


class BrowserHost(Protocol):
    """앱 전용 프로필로 띄운 브라우저 하나의 수명주기 (§A1, D6).

    계약:
    - 지연 기동: 생성만으로는 아무것도 띄우지 않는다. 첫 `page()` 가 띄운다.
    - 프로필 디렉터리는 한 번에 한 호스트만 쓴다. 다른 호스트(다른 프로세스 포함)가 쓰고 있으면
      `page()` 가 `BrowserProfileInUse`. 잠금은 첫 기동에 잡고 `close()` 까지 쥔다(사람이 브라우저를
      닫은 사이에도). 기동이 실패하면 바로 푼다.
    - 사람이 브라우저를 닫았으면(`running` 이 False 가 됨) 다음 `page()` 가 다시 띄운다.
    - 브라우저를 찾지 못하면 `ChromeNotFound`, 찾았는데 기동이 실패하면 `BrowserLaunchFailed`.
    - `close()` 는 브라우저를 닫고 잠금을 푼다. 여러 번 불러도 되고, 그 뒤 `page()` 는 다시 띄운다.
    """

    @property
    def running(self) -> bool:
        """브라우저가 지금 떠 있는가 (사람이 닫았으면 False)."""
        ...

    async def page(self) -> PageHandle:
        """작업 탭. 떠 있지 않으면 띄우고, 열린 탭이 없으면 새 탭을 연다.

        직전에 돌려준 탭이 아직 열려 있으면 같은 핸들을 돌려준다.
        """
        ...

    async def close(self) -> None: ...


class PageDriver(Protocol):
    """BrowserHost 가 준 탭에서 하는 DOM 동작 (§A5). BrowserToolbox 의 도구가 이것만 쓴다.

    요소는 **가장 최근 snapshot 의 ref** 로만 가리킨다. 계약:
    - `snapshot()` 은 탭의 모든 프레임을 훑어 조작 가능한 요소에 새 ref 를 매긴다. 이전 snapshot 의
      ref 는 그 순간 전부 무효다(번호도 다시 쓰지 않는다). 비밀번호·인증 코드 칸의 값은 읽지 않는다.
    - `navigate()`·`back()` 뒤에도 ref 는 무효다. 페이지가 스스로 바뀌어 요소가 사라져도 마찬가지다.
    - 요소 동작은 **그 순간의 DOM** 으로 대상을 다시 확인한다 — snapshot 이후 type 이 password 로
      바뀐 칸에는 fill 하지 않는다(절대 규칙 3).
    - 실패는 `PageActionFailed(reason)` 이고, 그때 페이지에는 아무 입력도 하지 않았다.
    - 키 입력(Enter)·클릭 좌표·임의 스크립트 동작은 없다(§A4 L1).
      `check` 는 네이티브 checkbox·radio 만.
    """

    async def snapshot(self, page: PageHandle) -> PageSnapshot: ...

    async def navigate(self, page: PageHandle, url: str) -> None: ...

    async def back(self, page: PageHandle) -> None: ...

    async def scroll(self, page: PageHandle, ref: str | None, *, down: bool) -> None: ...

    async def wait_for_text(self, page: PageHandle, text: str, *, timeout_ms: int) -> bool:
        """어느 프레임에든 보이는 글자로 나타나면 True, 시간 안에 없으면 False."""
        ...

    async def fill(self, page: PageHandle, ref: str, value: str) -> None: ...

    async def select(self, page: PageHandle, ref: str, option: str) -> str:
        """네이티브 select 에서 라벨(없으면 value)이 `option` 인 선택지를 고른다.

        고른 라벨을 돌려준다.
        """
        ...

    async def set_checked(self, page: PageHandle, ref: str, on: bool) -> None: ...

    async def upload(self, page: PageHandle, ref: str, file: UploadFile) -> None:
        """파일 입력에 바이트를 넣는다 — 디스크 경로를 받지 않는다."""
        ...
