"""BrowserToolbox (§A5) — 대역 드라이버 위에서 도구 호출·FillLog·거부 규칙."""

from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_pages import FakeElement, FakePageDriver
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.fill_log import FillAction, FillSourceKind
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.toolbox_kit import FakeDocuments
from tests.toolbox_pages import FAKE_BASE, FORM, FRAME, INNER, NEXT, fake_sites

RRN = "900101-1234567"
PROFILE = {"kind": "profile", "key": "name"}


@pytest.fixture
async def host(tmp_path: Path):
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    yield host
    await host.close()


@pytest.fixture
def driver(host) -> FakePageDriver:
    return FakePageDriver(host, fake_sites())


@pytest.fixture
def documents() -> FakeDocuments:
    return FakeDocuments({"doc_resume1": b"%PDF-1.7\n"})


@pytest.fixture
def toolbox(host, driver, documents) -> BrowserToolbox:
    return BrowserToolbox(
        host,
        driver,
        documents,
        user_id="local",
        application_id="app_1",
        run_id="run_1",
        forbidden_origins=["http://127.0.0.1:8000"],
    )


async def _open(toolbox: BrowserToolbox, path: str = FORM) -> dict[str, str]:
    assert (await toolbox.call("navigate", {"url": FAKE_BASE + path})).ok
    result = await toolbox.call("snapshot")
    assert result.ok and result.snapshot is not None
    return {n.name: n.ref for n in result.snapshot.nodes if n.ref}


def _err(result: ToolResult, error: ToolError) -> None:
    assert (result.ok, result.error) == (False, error), result


async def test_fill_select_check_upload_are_logged_with_sources(host, toolbox, driver):
    refs = await _open(toolbox)
    calls = [
        ("fill", {"ref": refs["이름"], "value": "홍길동", "source": PROFILE}),
        ("select", {"ref": refs["경력"], "option": "1~3년", "source": {"kind": "user"}}),
        ("check", {"ref": refs["개인정보 수집 동의"], "on": True, "source": {"kind": "user"}}),
        ("upload", {"ref": refs["이력서"], "document_id": "doc_resume1"}),
    ]
    for tool, args in calls:
        assert (await toolbox.call(tool, args)).ok, tool
    log = toolbox.fill_log.entries
    assert [e.action for e in log] == [
        FillAction.FILL, FillAction.SELECT, FillAction.CHECK, FillAction.UPLOAD
    ]  # fmt: skip
    assert [e.seq for e in log] == [1, 2, 3, 4]
    fill, select, check, upload = log
    assert (fill.value, fill.source.kind, fill.source.key) == (
        "홍길동",
        FillSourceKind.PROFILE,
        "name",
    )
    assert (fill.field.role, fill.field.name, fill.field.url) == (
        "textbox",
        "이름",
        FAKE_BASE + FORM,
    )
    assert select.value == "1~3년"
    assert check.checked is True
    assert (upload.document_id, upload.source) == ("doc_resume1", None)
    uploaded = next(e for e in driver.document(await host.page()).elements if e.file)
    assert uploaded.file.data == b"%PDF-1.7\n"


async def test_failed_actions_are_not_logged(toolbox):
    refs = await _open(toolbox)
    _err(
        await toolbox.call("select", {"ref": refs["경력"], "option": "10년", "source": PROFILE}),
        ToolError.OPTION_NOT_FOUND,
    )
    _err(
        await toolbox.call("fill", {"ref": refs["지원하기"], "value": "x", "source": PROFILE}),
        ToolError.UNSUPPORTED_ELEMENT,
    )
    assert toolbox.fill_log.entries == ()


async def test_iframe_entry_records_frame_url(toolbox):
    refs = await _open(toolbox, FRAME)
    args = {"ref": refs["포트폴리오 URL"], "value": "https://x.dev", "source": {"kind": "user"}}
    assert (await toolbox.call("fill", args)).ok
    assert toolbox.fill_log.entries[0].field.url == FAKE_BASE + INNER


@pytest.mark.parametrize("name", ["비밀번호", "인증번호"])
async def test_secret_fields_refused_before_the_driver(host, toolbox, driver, monkeypatch, name):
    refs = await _open(toolbox)
    reached: list[str] = []

    async def fill(page, ref, value):
        reached.append(ref)  # 드라이버의 두 번째 방어선까지 가면 안 된다

    monkeypatch.setattr(driver, "fill", fill)
    result = await toolbox.call("fill", {"ref": refs[name], "value": "hunter2", "source": PROFILE})
    _err(result, ToolError.SECRET_FIELD)
    assert reached == []
    assert toolbox.fill_log.entries == ()


async def test_field_turned_secret_after_snapshot_is_refused_by_the_driver(host, toolbox, driver):
    refs = await _open(toolbox)
    doc = driver.document(await host.page())
    next(e for e in doc.elements if e.name == "이름").type = "password"
    result = await toolbox.call("fill", {"ref": refs["이름"], "value": "pw", "source": PROFILE})
    _err(result, ToolError.SECRET_FIELD)
    assert toolbox.fill_log.entries == ()


async def test_unregistered_document_upload_refused(toolbox, documents):
    refs = await _open(toolbox)
    for doc in ("doc_unknown", "doc_resume2"):
        result = await toolbox.call("upload", {"ref": refs["이력서"], "document_id": doc})
        _err(result, ToolError.DOCUMENT_NOT_FOUND)
    _err(
        await toolbox.call("upload", {"ref": refs["이력서"], "document_id": "/etc/passwd"}),
        ToolError.INVALID_INPUT,
    )
    assert ("local", "/etc/passwd") not in documents.reads  # 경로는 저장소까지 가지도 않는다
    assert toolbox.fill_log.entries == ()


async def test_resident_number_value_is_filled_but_withheld_from_the_log(host, toolbox, driver):
    refs = await _open(toolbox)
    args = {"ref": refs["이름"], "value": f"번호 {RRN}", "source": {"kind": "user"}}
    assert (await toolbox.call("fill", args)).ok
    entry = toolbox.fill_log.entries[0]
    assert (entry.withheld, entry.value) == (True, None)
    assert RRN not in toolbox.fill_log.model_dump_json()
    # 사이트에는 들어갔고, 에이전트가 보는 snapshot 에서는 가려진다
    el = next(e for e in driver.document(await host.page()).elements if e.name == "이름")
    assert RRN in el.value
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None and RRN not in snap.model_dump_json()


async def test_snapshot_redacts_resident_numbers_in_page_text(host, toolbox, driver):
    await _open(toolbox)
    page = await host.page()
    driver.document(page).elements.append(FakeElement("text", f"주민번호 {RRN}", tag="p"))
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None and RRN not in snap.model_dump_json()


async def test_stale_refs_after_navigation(toolbox):
    refs = await _open(toolbox)
    assert (await toolbox.call("navigate", {"url": FAKE_BASE + NEXT})).ok
    result = await toolbox.call("fill", {"ref": refs["이름"], "value": "x", "source": PROFILE})
    _err(result, ToolError.STALE_REF)
    assert (await toolbox.call("back")).ok
    _err(await toolbox.call("scroll", {"ref": refs["이름"]}), ToolError.STALE_REF)


async def test_fill_before_any_snapshot_is_stale(toolbox):
    result = await toolbox.call("fill", {"ref": "e1", "value": "x", "source": PROFILE})
    _err(result, ToolError.STALE_REF)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost:8000/applications",
        "http://127.0.0.2:8000/",
        "http://[::1]:8000/",
        "https://LOCALHOST:8000/",
    ],
)
async def test_app_console_is_forbidden(toolbox, url):
    _err(await toolbox.call("navigate", {"url": url}), ToolError.FORBIDDEN_URL)


async def test_navigate_rejects_script_urls(toolbox):
    result = await toolbox.call("navigate", {"url": "javascript:document.forms[0].submit()"})
    _err(result, ToolError.INVALID_INPUT)


async def test_invalid_input_message_does_not_echo_values(toolbox):
    await _open(toolbox)
    result = await toolbox.call("fill", {"ref": RRN, "value": "x", "source": PROFILE})
    _err(result, ToolError.INVALID_INPUT)
    assert "ref" in result.message and RRN not in result.message


async def test_unknown_and_forbidden_tools(toolbox):
    for tool in ("click", "evaluate", "press", "mouse_click", "type"):
        _err(await toolbox.call(tool, {}), ToolError.UNKNOWN_TOOL)


async def test_wait_for(host, driver, documents):
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    toolbox = BrowserToolbox(
        host, driver, documents, user_id="local", application_id=None, run_id=None,
        sleep=sleep,
    )  # fmt: skip
    await _open(toolbox, NEXT)
    assert (await toolbox.call("wait_for", {"ms": 250})).ok
    assert slept == [0.25]
    assert (await toolbox.call("wait_for", {"text": "불러오기 완료"})).found is True
    assert (await toolbox.call("wait_for", {"text": "없는 문구"})).found is False


async def test_report_failure_ends_the_run(toolbox):
    refs = await _open(toolbox)
    assert (await toolbox.call("report_failure", {"reason": f"막힘 {RRN}"})).ok
    assert toolbox.failure is not None and RRN not in toolbox.failure
    for tool, args in [
        ("snapshot", {}),
        ("fill", {"ref": refs["이름"], "value": "x", "source": PROFILE}),
    ]:
        _err(await toolbox.call(tool, args), ToolError.RUN_FINISHED)
