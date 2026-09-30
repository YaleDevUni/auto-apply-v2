"""BrowserHost contract test (§A2) — 실제(Playwright + 설치 Chrome, native)와 대역에 같은
기대를 건다.

"사람이 닫았다"는 구현마다 흉내 내는 방법이 달라 픽스처가 함께 준다: 대역은 상태만 바꾸고,
실제는 CDP 로 브라우저 프로세스를 스스로 끝내게 한다.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.domain.errors import BrowserProfileInUse
from auto_apply.ports.browser import BrowserHost, PageHandle
from tests.browsers import close_tabs_like_human, quit_like_human, real_host

Human = Callable[[BrowserHost, PageHandle], Awaitable[None]]


@dataclass
class Kit:
    make: Callable[[Path], BrowserHost]
    human_quit: Human
    human_close_tabs: Human


async def _fake_quit(host: BrowserHost, _: PageHandle) -> None:
    assert isinstance(host, FakeBrowserHost)
    host.simulate_human_close()


async def _fake_close_tabs(host: BrowserHost, _: PageHandle) -> None:
    assert isinstance(host, FakeBrowserHost)
    host.close_all_tabs()


async def _real_quit(host: BrowserHost, handle: PageHandle) -> None:
    await quit_like_human(host, handle)  # type: ignore[arg-type]


async def _real_close_tabs(host: BrowserHost, handle: PageHandle) -> None:
    await close_tabs_like_human(host, handle)  # type: ignore[arg-type]


@pytest.fixture(params=["fake", pytest.param("playwright", marks=pytest.mark.native)])
async def kit(request):
    made: list[BrowserHost] = []

    def make(profile: Path) -> BrowserHost:
        host: BrowserHost = (
            FakeBrowserHost(profile) if request.param == "fake" else real_host(profile)
        )
        made.append(host)
        return host

    if request.param == "fake":
        yield Kit(make, _fake_quit, _fake_close_tabs)
    else:
        yield Kit(make, _real_quit, _real_close_tabs)
    for host in made:  # 실패한 테스트도 Chrome 을 남기지 않는다
        await host.close()


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    return tmp_path / "data" / "chrome-profile"


async def test_lazy_launch(kit, profile):
    host = kit.make(profile)
    assert host.running is False
    assert not profile.exists()  # 생성만으로는 아무것도 띄우지 않는다
    await host.page()
    assert host.running is True


async def test_same_tab_until_closed(kit, profile):
    host = kit.make(profile)
    first = await host.page()
    assert await host.page() == first


async def test_new_tab_when_human_closed_all_tabs(kit, profile):
    host = kit.make(profile)
    old = await host.page()
    await kit.human_close_tabs(host, old)
    new = await host.page()
    assert new != old
    assert host.running is True
    assert await host.page() == new


async def test_close_is_idempotent_and_relaunchable(kit, profile):
    host = kit.make(profile)
    old = await host.page()
    await host.close()
    await host.close()
    assert host.running is False
    new = await host.page()
    assert host.running is True
    assert new != old  # 재기동 전 핸들이 새 탭을 가리키지 않는다


async def test_relaunch_after_human_quit(kit, profile):
    host = kit.make(profile)
    old = await host.page()
    await kit.human_quit(host, old)
    assert host.running is False
    new = await host.page()
    assert host.running is True
    assert new != old


async def test_second_host_on_same_profile_refused(kit, profile):
    first = kit.make(profile)
    await first.page()
    second = kit.make(profile)
    with pytest.raises(BrowserProfileInUse):
        await second.page()
    assert second.running is False
    await first.close()
    await second.page()  # 먼저 뜬 쪽이 끝나면 쓸 수 있다
    assert second.running is True


async def test_human_quit_keeps_profile_lock(kit, profile):
    """사람이 창을 닫은 사이에 다른 호스트가 프로필을 가로채지 않는다."""
    first = kit.make(profile)
    await kit.human_quit(first, await first.page())
    with pytest.raises(BrowserProfileInUse):
        await kit.make(profile).page()


async def test_different_profiles_coexist(kit, tmp_path):
    a = kit.make(tmp_path / "a" / "chrome-profile")
    b = kit.make(tmp_path / "b" / "chrome-profile")
    await a.page()
    await b.page()
    assert a.running and b.running
