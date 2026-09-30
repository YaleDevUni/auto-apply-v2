"""GuardedPageDriver(= PageDriver + 하네스) 두 구현(대역 · 실제=native)을 같은 모양으로 꺼내는
pytest 픽스처.

실제 쪽은 짐 서버를 tests/fixtures/toolbox/ 루트로 띄워 127.0.0.1 에서 서빙한다. "페이지가 스스로
바뀐다"·"사람이 탭을 닫았다" 는 구현마다 흉내 내는 방법이 달라 kit 이 함께 준다.
`FakeDocuments` 는 BrowserToolbox 의 문서 통로(DocumentReader) 대역이다.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.contracts.knowledge import DocumentMeta
from auto_apply.domain.errors import NotFound
from auto_apply.ports.browser import BrowserHost, GuardedPageDriver, PageHandle
from tests.browsers import close_tabs_like_human, real_host
from tests.gym.server import GymServer
from tests.toolbox_pages import FAKE_BASE, TOOLBOX_DIR, fake_sites


class FakeDocuments:
    def __init__(self, docs: dict[str, bytes]) -> None:
        self.docs = docs
        self.reads: list[tuple[str, str]] = []

    async def read_document(self, user_id: str, document_id: str) -> tuple[DocumentMeta, bytes]:
        self.reads.append((user_id, document_id))
        if user_id != "local" or document_id not in self.docs:
            raise NotFound(document_id)
        meta = DocumentMeta(
            id=document_id,
            user_id=user_id,
            filename="resume.pdf",
            content_type="application/pdf",
            size_bytes=len(self.docs[document_id]),
            blob_key=f"documents/{user_id}/{document_id}.pdf",
            created_at=datetime(2026, 9, 30, tzinfo=UTC),
        )
        return meta, self.docs[document_id]


@dataclass
class DriverKit:
    kind: str
    host: BrowserHost
    driver: GuardedPageDriver
    base: str
    # 라벨이 `name` 인 칸의 type 을 password 로 바꾼다 (snapshot 뒤 페이지 스스로의 변화)
    make_secret: Callable[[PageHandle, str], Awaitable[None]]
    close_tabs: Callable[[PageHandle], Awaitable[None]]
    gym: GymServer | None = None

    def url(self, path: str) -> str:
        return self.base + path


def _fake_kit(profile: Path) -> DriverKit:
    host = FakeBrowserHost(profile)
    driver = FakeGuardedPageDriver(host, fake_sites())

    async def make_secret(page: PageHandle, name: str) -> None:
        for el in driver.document(page).elements:
            if el.name == name:
                el.type = "password"

    async def close_tabs(_: PageHandle) -> None:
        host.close_all_tabs()

    return DriverKit("fake", host, driver, FAKE_BASE, make_secret, close_tabs)


def _real_kit(profile: Path, gym: GymServer) -> DriverKit:
    host: PlaywrightBrowserHost = real_host(profile)

    async def make_secret(page: PageHandle, name: str) -> None:
        await host.vendor_page(page).get_by_label(name).evaluate("el => { el.type = 'password'; }")

    async def close_tabs(page: PageHandle) -> None:
        await close_tabs_like_human(host, page)

    return DriverKit(
        "playwright",
        host,
        PlaywrightGuardedPageDriver(host),
        gym.base_url,
        make_secret,
        close_tabs,
        gym,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _shared_real_kit(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[DriverKit]:
    """Chrome 기동이 테스트 시간의 대부분이라 모듈에서 한 번만 띄운다. 격리는 `driver_kit` 이
    테스트마다 탭을 새로 여는 것으로 지킨다(히스토리·ref 표가 테스트를 건너가지 않는다).

    BrowserHost 는 지연 기동이라 대역만 도는 실행(make check)에서는 Chrome 이 뜨지 않는다.
    """
    profile = tmp_path_factory.mktemp("toolbox") / "data" / "chrome-profile"
    with GymServer(root=TOOLBOX_DIR) as gym:
        kit = _real_kit(profile, gym)
        try:
            yield kit
        finally:  # 실패한 테스트도 Chrome 을 남기지 않는다
            await kit.host.close()


@pytest_asyncio.fixture(
    loop_scope="module", params=["fake", pytest.param("playwright", marks=pytest.mark.native)]
)
async def driver_kit(
    request: pytest.FixtureRequest, tmp_path: Path, _shared_real_kit: DriverKit
) -> AsyncIterator[DriverKit]:
    """쓰는 테스트 모듈은 `pytestmark = pytest.mark.asyncio(loop_scope="module")` 이어야 한다."""
    if request.param == "fake":
        kit = _fake_kit(tmp_path / "data" / "chrome-profile")
        yield kit
        await kit.host.close()
        return
    kit = _shared_real_kit
    await kit.close_tabs(await kit.host.page())  # 다음 page() 가 히스토리 없는 새 탭을 연다
    assert kit.gym is not None
    kit.gym.clear()
    yield kit
