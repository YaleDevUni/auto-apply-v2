"""BrowserToolbox 를 실제 브라우저로 (native) — 짐 서버가 받은 요청으로 확인한다(§A4 테스트 짐)."""

from pathlib import Path

import pytest

from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from auto_apply.contracts.browser_tools import ToolError
from auto_apply.contracts.fill_log import FillAction
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.browsers import real_host
from tests.gym.fixtures import gym
from tests.gym.server import GymServer
from tests.toolbox_kit import FakeDocuments
from tests.toolbox_pages import FORM, PASSWORD_VALUE, TOOLBOX_DIR

pytestmark = pytest.mark.native
__all__ = ["gym"]


@pytest.fixture
async def host(tmp_path: Path):
    host = real_host(tmp_path / "data" / "chrome-profile")
    yield host
    await host.close()


@pytest.fixture
def toolbox_site():
    with GymServer(root=TOOLBOX_DIR) as server:
        yield server


def _toolbox(host: PlaywrightBrowserHost) -> BrowserToolbox:
    return BrowserToolbox(
        host,
        PlaywrightGuardedPageDriver(host),
        FakeDocuments({"doc_resume1": b"%PDF-1.7\n"}),
        user_id="local",
        application_id="app_native",
        run_id="run_native",
    )


async def _refs(toolbox: BrowserToolbox, url: str) -> dict[str, str]:
    assert (await toolbox.call("navigate", {"url": url})).ok
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    assert PASSWORD_VALUE not in snap.model_dump_json()
    return {n.name: n.ref for n in snap.nodes if n.ref}


async def test_snapshot_fill_filllog_on_fixture_form(host, toolbox_site):
    toolbox = _toolbox(host)
    refs = await _refs(toolbox, toolbox_site.url(FORM))
    profile = {"kind": "profile", "key": "name"}
    # 한 줄 칸에 줄바꿈을 넣어도 Enter 가 아니다 — 암묵 제출이 일어나면 안 된다
    assert (
        await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동\n", "source": profile})
    ).ok
    args = {"ref": refs["경력"], "option": "1년 미만", "source": {"kind": "user"}}
    assert (await toolbox.call("select", args)).ok
    args = {"ref": refs["개인정보 수집 동의"], "on": True, "source": {"kind": "user"}}
    assert (await toolbox.call("check", args)).ok
    assert (await toolbox.call("upload", {"ref": refs["이력서"], "document_id": "doc_resume1"})).ok
    pw = {"ref": refs["비밀번호"], "value": "hunter2", "source": profile}
    assert (await toolbox.call("fill", pw)).error is ToolError.SECRET_FIELD
    bad = {"ref": refs["포트폴리오"], "document_id": "doc_not_registered"}
    assert (await toolbox.call("upload", bad)).error is ToolError.DOCUMENT_NOT_FOUND

    page = host.vendor_page(await host.page())
    assert await page.get_by_label("이름").input_value() == "홍길동"
    assert await page.get_by_label("비밀번호").input_value() == PASSWORD_VALUE  # 손대지 않았다
    assert await page.get_by_label("이력서").evaluate("el => el.files[0].name") == "resume.pdf"
    assert await page.get_by_label("포트폴리오").evaluate("el => el.files.length") == 0
    assert [e.action for e in toolbox.fill_log.entries] == [
        FillAction.FILL, FillAction.SELECT, FillAction.CHECK, FillAction.UPLOAD
    ]  # fmt: skip
    assert [r.method for r in toolbox_site.requests if r.method not in ("GET", "HEAD")] == []


async def test_filling_a_gym_site_submits_nothing(host, gym):
    toolbox = _toolbox(host)
    refs = await _refs(toolbox, gym.entry_url("multipart_form"))
    source = {"kind": "profile", "key": "name"}
    assert (
        await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동\n", "source": source})
    ).ok
    assert (await toolbox.call("upload", {"ref": refs["이력서"], "document_id": "doc_resume1"})).ok
    await toolbox.call("wait_for", {"ms": 300})
    assert gym.final_submissions() == []
    assert gym.unexpected_requests() == []
