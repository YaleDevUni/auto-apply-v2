from dataclasses import dataclass
from typing import Protocol

from auto_apply.contracts.click import ElementDescriptor
from auto_apply.contracts.page import PageSnapshot, UploadFile
from auto_apply.contracts.submit_guard import GuardReport, PageText, SubmitTarget
from auto_apply.domain.submit_guard_policy import GuardMode


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


class GuardedPageDriver(PageDriver, Protocol):
    """PageDriver 에 SubmitGuard(§A4 L3·L4·L5) 장치를 더한 것.

    구현은 adapters/browser/playwright_guarded.py(실제)·fake_guard.py(대역)이고
    tests/ports/test_guarded_driver_contract.py 가 둘에 같은 기대를 건다. `click` 은 이 Protocol
    에만 있다 — 하네스를 거치지 않는 클릭 통로는 없다. 모드를 바꾸는 입구(`set_window`)는
    SubmitGuard 만 부르고, 도구 인자·가이드·프롬프트는 여기에 닿지 않는다.

    계약:
    - `arm()` 은 탭이 속한 브라우저 전체(모든 탭·프레임)에 네트워크 차단·페이지 스크립트 차단·
      대화상자 처리를 건다. 이미 걸려 있으면 아무것도 안 한다(브라우저가 재기동됐으면 다시 건다).
      못 걸면 `SubmitGuardUnavailable` — 그때 호출자는 페이지를 건드리지 않는다.
      막 켠 모드는 strict 다.
    - `set_window()` 는 다음 `set_window()` 까지의 모드를 정한다(창이 끝나도 유지된다).
      `carried` 는 입력한 값 — 이 값을 URL 에 싣는 문서 탐색은 모든 모드에서, GET 요청은 strict 에서
      막는다. 가드가 켜져 있지 않으면 `SubmitGuardUnavailable`.
    - `drain()` 은 직전 `drain()` 뒤로 막은 것·처리한 대화상자를 돌려주고 비운다.
    - `settle()` 은 방금 한 동작의 후속(핸들러의 비동기 요청·탐색)이 잦아들 때까지 상한 안에서
      기다린다.
    - `disarm()` 뒤에는 앱 출처 차단만 남는다(사람이 브라우저를 쓰는 동안).
    - `describe()` 는 ref 요소와 클릭이 닿는 조작 가능한 조상의 **지금** DOM 기술(가장 안쪽부터).
    - `click()` 은 가드가 서 있지 않으면 `SubmitGuardUnavailable`, 파일 입력·비밀 칸은 거부한다
      (upload 도구·절대 규칙 3). 좌표·키 입력은 없다.
    """

    async def arm(self, page: PageHandle, *, forbidden_origins: tuple[str, ...]) -> None: ...

    async def disarm(self, page: PageHandle) -> None: ...

    async def set_window(
        self, page: PageHandle, mode: GuardMode, *, carried: tuple[str, ...]
    ) -> None: ...

    async def settle(self, page: PageHandle) -> None: ...

    async def drain(self, page: PageHandle) -> GuardReport: ...

    async def describe(self, page: PageHandle, ref: str) -> tuple[ElementDescriptor, ...]: ...

    async def click(self, page: PageHandle, ref: str) -> None: ...

    async def read_text(self, page: PageHandle) -> PageText:
        """모든 프레임의 URL·보이는 글자 줄. 읽을 수 없는 프레임은 건너뛴다(예외 없음)."""
        ...

    async def submit_target(self, page: PageHandle, ref: str) -> SubmitTarget: ...
