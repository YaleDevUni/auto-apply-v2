from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from auto_apply.adapters.browser.profile_lock import ProfileLock
from auto_apply.ports.browser import PageHandle


@dataclass
class FakePage:
    handle: PageHandle
    url: str = "about:blank"
    closed: bool = False


@dataclass
class _Session:
    pages: list[FakePage] = field(default_factory=list)


class FakeBrowserHost:
    """테스트 대역 — 메모리 페이지 모델. 실제 구현과 같은 프로필 잠금을 쓴다.

    `simulate_human_close()`·`close_all_tabs()` 로 사람이 브라우저·탭을 닫은 상황을 흉내 낸다.
    """

    def __init__(self, profile_dir: Path) -> None:
        self._lock = ProfileLock(profile_dir)
        self._session: _Session | None = None
        self._current: FakePage | None = None
        self._ids = count(1)

    @property
    def running(self) -> bool:
        return self._session is not None

    async def page(self) -> PageHandle:
        if self._session is None:
            self._lock.acquire()
            self._session = _Session()
            self._current = None
        if self._current is not None and not self._current.closed:
            return self._current.handle
        open_pages = [p for p in self._session.pages if not p.closed]
        if open_pages:
            self._current = open_pages[-1]
        else:
            self._current = FakePage(PageHandle(f"fake-page-{next(self._ids)}"))
            self._session.pages.append(self._current)
        return self._current.handle

    async def close(self) -> None:
        self._session = None
        self._current = None
        self._lock.release()

    def simulate_human_close(self) -> None:
        """사람이 브라우저를 종료했다 — 잠금은 호스트가 쥔 채로 남는다(실제 구현과 같다)."""
        self._session = None
        self._current = None

    def close_all_tabs(self) -> None:
        """사람이 탭을 전부 닫았다 — 브라우저 프로세스는 남는다(mac Chrome 처럼)."""
        for p in [] if self._session is None else self._session.pages:
            p.closed = True
