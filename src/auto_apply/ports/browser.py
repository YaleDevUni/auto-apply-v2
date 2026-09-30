from dataclasses import dataclass
from typing import Protocol


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
