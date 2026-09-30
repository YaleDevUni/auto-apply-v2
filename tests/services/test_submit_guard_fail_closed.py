"""SubmitGuard 가 뜻밖의 실패에서 닫힌 쪽으로 남는지 (§A4, 절대 규칙 1).

- 동작이 예상 밖 예외로 끝나도 strict 꼬리는 걸린다(핸들러는 이미 돌았을 수 있다).
- route 처리기는 판정·기록이 실패해도 요청을 abort 한다(흘려보내지 않는다).
- 안전 라벨 클릭이 입력값을 실어 문서를 여는 GET 폼 제출은 relaxed 에서도 막힌다.
"""

from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.playwright_guard import ContextGuard
from auto_apply.contracts.browser_tools import ToolError
from auto_apply.domain.submit_guard_policy import BlockReason, GuardMode
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.submit_guard import STRICT_TAIL_S, SubmitGuard
from tests.guard_fake_pages import FORM, NEXT, guard_sites
from tests.toolbox_kit import FakeDocuments

USER = {"kind": "user"}


@pytest.fixture
async def host(tmp_path: Path):
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    yield host
    await host.close()


async def test_strict_tail_holds_even_when_the_action_raises(host):
    driver = FakeGuardedPageDriver(host, guard_sites())
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    guard = SubmitGuard(driver, forbidden_origins=(), sleep=sleep, clock=lambda: 100.0)
    page = await host.page()
    await guard.arm(page)

    async def boom() -> None:
        raise RuntimeError("드라이버 버그")

    with pytest.raises(RuntimeError):
        await guard.run(page, GuardMode.STRICT, boom)

    async def noop() -> None:
        return None

    await guard.run(page, GuardMode.RELAXED, noop)
    assert slept == [STRICT_TAIL_S]


class _Route:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def continue_(self) -> None:
        self.calls.append("continue")

    async def abort(self, code: str) -> None:
        self.calls.append(f"abort:{code}")


class _BrokenRequest:
    """판정·기록에 쓰는 속성이 깨진 요청 — 그래도 흘려보내면 안 된다."""

    url = "https://jobs.example.com/api/submit"
    resource_type = "fetch"

    @property
    def method(self) -> str:
        raise RuntimeError("깨진 요청")

    def is_navigation_request(self) -> bool:
        return False


async def test_route_aborts_when_verdict_and_record_fail():
    context = type("NoPages", (), {"pages": []})()
    guard = ContextGuard(context, "token")  # type: ignore[arg-type]  # _route·drain 만 본다
    guard.armed = True
    route = _Route()
    await guard._route(route, _BrokenRequest())  # type: ignore[arg-type]
    assert route.calls == ["abort:blockedbyclient"]
    report = await guard.drain()
    assert [b.reason for b in report.blocked] == [BlockReason.GUARD_ERROR]


async def test_safe_label_get_form_submission_is_blocked_in_relaxed(host):
    driver = FakeGuardedPageDriver(host, guard_sites())
    toolbox = BrowserToolbox(host, driver, FakeDocuments({}), user_id="local",
                             application_id="app_f", run_id="run_f")  # fmt: skip
    assert (await toolbox.call("navigate", {"url": FORM})).ok
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    refs = {n.name: n.ref for n in snap.nodes if n.ref}
    assert (await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": USER})).ok

    result = await toolbox.call("click", {"ref": refs["검색"]})
    assert (result.ok, result.error) == (False, ToolError.SUBMIT_BLOCKED)
    assert [b.reason for b in result.guard.blocked] == [BlockReason.CARRIES_INPUT]
    assert all(not url.startswith(NEXT) for _, url in driver.sent)
    # 입력값을 싣는 요청(자동 완성)은 relaxed 에서 통과한다 — 막는 것은 문서 탐색뿐
    assert (await toolbox.call("click", {"ref": refs["주소 검색"]})).ok
    assert driver.sent[-1][1].startswith("http://site.test/api/address")
