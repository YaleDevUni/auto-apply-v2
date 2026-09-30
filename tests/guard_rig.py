"""SubmitGuard native 테스트의 공용 장치 — 모듈에서 Chrome 하나·짐 서버 하나 (§A4 T2.5).

Chrome 기동이 테스트 시간의 대부분이라 모듈에서 한 번만 띄우고, 테스트마다 탭을 닫아 새 탭에서
시작한다. 쓰는 모듈은 `pytestmark = pytest.mark.asyncio(loop_scope="module")` 이어야 한다.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.ports.human_gate import HumanGate
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.browsers import close_tabs_like_human, real_host
from tests.gym.server import GymServer
from tests.toolbox_kit import FakeDocuments


class Rig:
    def __init__(self, profile: Path, gym: GymServer) -> None:
        self.host = real_host(profile)
        self.driver = PlaywrightGuardedPageDriver(self.host)
        self.gym = gym

    def toolbox(self, gate: HumanGate | None = None, human_wait_s: float = 30) -> BrowserToolbox:
        return BrowserToolbox(
            self.host, self.driver, FakeDocuments({}), human_gate=gate or InMemoryHumanGate(),
            human_wait_s=human_wait_s, user_id="local", application_id="app_gym", run_id="run_gym",
            forbidden_origins=["http://127.0.0.1:8000"],
        )  # fmt: skip


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _rig(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Rig]:
    with GymServer() as gym:
        rig = Rig(tmp_path_factory.mktemp("guard") / "data" / "chrome-profile", gym)
        try:
            yield rig
        finally:  # 실패한 테스트도 Chrome 을 남기지 않는다
            await rig.host.close()


@pytest_asyncio.fixture(loop_scope="module")
async def rig(_rig: Rig) -> AsyncIterator[Rig]:
    await close_tabs_like_human(_rig.host, await _rig.host.page())
    _rig.gym.clear()
    yield _rig
