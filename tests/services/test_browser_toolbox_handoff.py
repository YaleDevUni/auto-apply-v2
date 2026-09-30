"""사람 핸드오프 — request_login·request_human (§A5, 절대 규칙 1·3). 대역 드라이버 위에서.

핵심 경계: 사람은 로그인 폼을 제출할 수 있어야 하므로 기다리는 동안 가드가 꺼진다. 그 틈에
에이전트 도구는 하나도 돌지 않고, 가드가 다시 선 뒤에만 도구를 받는다.
실제 브라우저·짐 로그인 벽으로 같은 것을 보는 것은 test_login_handoff_native.py 다.
"""

import asyncio
from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_effects import Later, Send, SubmitForm
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTask, HumanTaskKind
from auto_apply.domain.human_handoff import HandoffSignal
from auto_apply.ports.human_gate import HumanGate
from auto_apply.services.browser_toolbox import BrowserToolbox
from tests.toolbox_kit import FakeDocuments

B = "http://site.test"
LOGIN, APPLY, DONE_PAGE, CAPTCHA = f"{B}/member/login.html", f"{B}/apply", f"{B}/done", f"{B}/cap"
RECAPTCHA = "https://www.google.com/recaptcha/api2/anchor?k=x&size=normal"
FINAL = Send("POST", f"{B}/api/submit")
DONE = HumanReply(outcome=HumanOutcome.DONE, note="로그인했어요")


def _sites() -> dict[str, FakeDocument]:
    submit = SubmitForm("POST", f"{B}/api/submit")
    return {
        LOGIN: FakeDocument(
            title="로그인",
            elements=[
                FakeElement("heading", "로그인", tag="h1"),
                FakeElement("textbox", "아이디", autocomplete="username"),
                FakeElement("textbox", "비밀번호", type="password"),
                FakeElement(
                    "button",
                    "로그인",
                    tag="button",
                    type="submit",
                    in_form=True,
                    default_button=True,
                    on_click=(SubmitForm("POST", f"{B}/api/login"),),
                ),
            ],
        ),
        APPLY: FakeDocument(
            title="지원서",
            elements=[
                FakeElement("heading", "지원서", tag="h1"),
                FakeElement("textbox", "이름", in_form=True),
                FakeElement(
                    "button", "늦게", tag="button", type="button", on_click=(Later((FINAL,)),)
                ),
                FakeElement(
                    "button",
                    "지원하기",
                    tag="button",
                    type="submit",
                    in_form=True,
                    default_button=True,
                    on_click=(submit, Send("POST", DONE_PAGE, navigation=True)),
                ),
            ],
        ),
        DONE_PAGE: FakeDocument(
            elements=[FakeElement("heading", "지원이 완료되었습니다", tag="h1")]
        ),
        CAPTCHA: FakeDocument(
            title="보안 확인",
            elements=[
                FakeElement("textbox", "이름"),
                FakeElement("checkbox", "로봇이 아닙니다.", type="checkbox", frame=1),
                FakeElement("textbox", "자동입력 방지 문자"),
            ],
            frames=(RECAPTCHA,),
        ),
    }


@pytest.fixture
async def host(tmp_path: Path):
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    yield host
    await host.close()


@pytest.fixture
def driver(host) -> FakeGuardedPageDriver:
    return FakeGuardedPageDriver(host, _sites())


@pytest.fixture
def slept() -> list[float]:
    return []


def _toolbox(
    host, driver, gate: HumanGate, slept: list[float], wait_s: float = 5
) -> BrowserToolbox:
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return BrowserToolbox(
        host, driver, FakeDocuments({}), human_gate=gate, human_wait_s=wait_s, user_id="local",
        application_id="app_h", run_id="run_h", sleep=sleep,
    )  # fmt: skip


async def _open(toolbox: BrowserToolbox, url: str) -> tuple[ToolResult, dict[str, str]]:
    assert (await toolbox.call("navigate", {"url": url})).ok
    snap = await toolbox.call("snapshot")
    assert snap.ok and snap.snapshot is not None
    return snap, {n.name: n.ref for n in snap.snapshot.nodes if n.ref}


def _err(result: ToolResult, error: ToolError) -> None:
    assert (result.ok, result.error) == (False, error), result


async def test_login_wall_is_hinted_and_password_is_never_typed(host, driver, slept):
    toolbox = _toolbox(host, driver, InMemoryHumanGate(), slept)
    snap, refs = await _open(toolbox, LOGIN)
    assert snap.handoff is HandoffSignal.PASSWORD_FIELD and "request_login" in snap.message
    args = {"ref": refs["비밀번호"], "value": "hunter2", "source": {"kind": "user"}}
    _err(await toolbox.call("fill", args), ToolError.SECRET_FIELD)
    _err(await toolbox.call("click", {"ref": refs["로그인"]}), ToolError.SUBMIT_BLOCKED)
    assert driver.sent == []  # 로그인 폼은 에이전트 손으로 나가지 않는다
    _, refs = await _open(toolbox, APPLY)
    assert (await toolbox.call("snapshot")).handoff is None


async def test_request_login_disarms_blocks_tools_and_rearms(host, driver, slept):
    during: dict[str, object] = {}

    async def human(task: HumanTask) -> HumanReply:
        during["armed"] = driver.armed
        during["awaiting"] = toolbox.awaiting_human
        # 기다리는 동안의 에이전트 호출 — 무엇이든 바로 거부된다(잠금을 기다리지도 않는다)
        during["click"] = await toolbox.call("click", {"ref": refs["로그인"]})
        during["snapshot"] = await toolbox.call("snapshot")
        during["bogus"] = await toolbox.call("rm -rf", {})
        page = await host.page()
        driver.human_click(page, "로그인")  # 사람의 로그인 폼 제출 — 가드가 꺼져 있어 나간다
        await driver.navigate(page, APPLY)  # 사이트가 지원 페이지로 돌려보낸다
        return DONE

    gate = ScriptedHumanGate([human])
    toolbox = _toolbox(host, driver, gate, slept)
    _, refs = await _open(toolbox, LOGIN)
    result = await toolbox.call("request_login", {"site": "짐 채용"})
    assert result.ok and "로그인했어요" in result.message, result
    assert during["armed"] is False and during["awaiting"] == gate.asked[0]
    for key in ("click", "snapshot"):
        _err(during[key], ToolError.AWAITING_HUMAN)  # type: ignore[arg-type]
    assert during["bogus"].tool == "unknown"  # type: ignore[attr-defined]
    assert ("POST", f"{B}/api/login") in driver.sent
    [task] = gate.asked
    assert (task.kind, task.site, task.signal) == (HumanTaskKind.LOGIN, "짐 채용", "password_field")
    assert (task.page_url, task.application_id, task.run_id) == (LOGIN, "app_h", "run_h")
    # 재개: 가드가 다시 섰고, 옛 ref 는 무효, 새 화면은 지원서다
    assert driver.armed and toolbox.awaiting_human is None and gate.pending() == ()
    _err(await toolbox.call("click", {"ref": refs["로그인"]}), ToolError.STALE_REF)
    snap = await toolbox.call("snapshot")
    assert snap.handoff is None and snap.snapshot is not None
    refs = {n.name: n.ref for n in snap.snapshot.nodes if n.ref}
    _err(await toolbox.call("click", {"ref": refs["지원하기"]}), ToolError.SUBMIT_BLOCKED)
    assert [s for s in driver.sent if s[1] == f"{B}/api/submit"] == []


async def test_timeout_ends_the_run_as_needs_login(host, driver, slept):
    gate = InMemoryHumanGate()
    toolbox = _toolbox(host, driver, gate, slept, wait_s=0.01)
    await _open(toolbox, LOGIN)
    _err(await toolbox.call("request_login", {"site": "짐"}), ToolError.NEEDS_LOGIN)
    assert toolbox.needs_human is not None and toolbox.needs_human.kind is HumanTaskKind.LOGIN
    _err(await toolbox.call("snapshot"), ToolError.RUN_FINISHED)
    assert not driver.armed  # 사람이 아직 로그인 중일 수 있다 — run 이 끝난 것과 같다
    await toolbox.close()
    assert gate.pending() == ()


async def test_declined_request_human_ends_the_run_as_needs_input(host, driver, slept):
    gate = ScriptedHumanGate([HumanReply(outcome=HumanOutcome.DECLINED)])
    toolbox = _toolbox(host, driver, gate, slept)
    await _open(toolbox, CAPTCHA)
    result = await toolbox.call("request_human", {"reason": "CAPTCHA 900101-1234567"})
    _err(result, ToolError.NEEDS_INPUT)
    assert toolbox.needs_human is not None and toolbox.needs_human.kind is HumanTaskKind.INPUT
    [task] = gate.asked
    assert "1234567" not in task.reason and task.signal is HandoffSignal.CAPTCHA


async def test_captcha_widgets_are_for_humans_only(host, driver, slept):
    toolbox = _toolbox(host, driver, ScriptedHumanGate([DONE]), slept)
    snap, refs = await _open(toolbox, CAPTCHA)
    assert snap.handoff is HandoffSignal.CAPTCHA and "request_human" in snap.message
    _err(await toolbox.call("check", {"ref": refs["로봇이 아닙니다."], "on": True,
                                      "source": {"kind": "user"}}), ToolError.CAPTCHA)  # fmt: skip
    _err(await toolbox.call("click", {"ref": refs["로봇이 아닙니다."]}), ToolError.CAPTCHA)
    fill = {"ref": refs["자동입력 방지 문자"], "value": "x7k2", "source": {"kind": "user"}}
    _err(await toolbox.call("fill", fill), ToolError.CAPTCHA)
    ok = {"ref": refs["이름"], "value": "홍길동", "source": {"kind": "user"}}
    assert (await toolbox.call("fill", ok)).ok
    assert (await toolbox.call("request_human", {"reason": "CAPTCHA 를 풀어 주세요"})).ok


async def test_failed_rearm_keeps_the_page_untouched(host, driver, slept):
    async def human(_: HumanTask) -> HumanReply:
        driver.fail_arm = True  # 사람이 돌아왔는데 가드를 다시 걸 수 없다
        return DONE

    toolbox = _toolbox(host, driver, ScriptedHumanGate([human]), slept)
    await _open(toolbox, LOGIN)
    _err(await toolbox.call("request_login", {"site": "짐"}), ToolError.GUARD_UNAVAILABLE)
    assert not driver.armed
    for tool, args in (("snapshot", {}), ("navigate", {"url": APPLY})):
        _err(await toolbox.call(tool, args), ToolError.GUARD_UNAVAILABLE)
    assert driver.sent == []
    driver.fail_arm = False
    assert (await toolbox.call("snapshot")).ok and driver.armed


async def test_delayed_submission_is_blocked_before_the_guard_goes_down(host, driver, slept):
    toolbox = _toolbox(host, driver, ScriptedHumanGate([DONE]), slept)
    _, refs = await _open(toolbox, APPLY)
    assert (await toolbox.call("click", {"ref": refs["늦게"]})).ok  # strict, 제출은 미뤄졌다
    result = await toolbox.call("request_login", {"site": "짐"})
    assert result.ok
    assert slept and slept[-1] > 0  # strict 꼬리를 다 기다린 뒤에 껐다
    assert [b.method for b in result.guard.blocked] == ["POST"]  # 미룬 제출은 켜진 가드가 막았다
    assert (FINAL.method, FINAL.url) not in driver.sent


async def test_application_submitted_during_handoff_is_an_incident(host, driver, slept):
    async def human(_: HumanTask) -> HumanReply:
        driver.human_click(await host.page(), "지원하기")  # 사람이 스스로 제출했다
        return DONE

    toolbox = _toolbox(host, driver, ScriptedHumanGate([human]), slept)
    await _open(toolbox, APPLY)
    _err(await toolbox.call("request_login", {"site": "짐"}), ToolError.INCIDENT)
    assert toolbox.incident is not None
    _err(await toolbox.call("snapshot"), ToolError.INCIDENT)


async def _until_pending(gate: InMemoryHumanGate) -> None:
    for _ in range(1000):  # 루프가 몇 번 돌면 등록된다 — 끝없이 돌지 않게 상한
        if gate.pending():
            return
        await asyncio.sleep(0)
    raise AssertionError("사람 대기가 등록되지 않았다")


async def test_concurrent_calls_are_refused_while_waiting(host, driver, slept):
    gate = InMemoryHumanGate()
    toolbox = _toolbox(host, driver, gate, slept)
    await _open(toolbox, LOGIN)
    waiting = asyncio.create_task(toolbox.call("request_login", {"site": "짐"}))
    await _until_pending(gate)
    results = await asyncio.gather(*(toolbox.call("snapshot") for _ in range(3)))
    assert {r.error for r in results} == {ToolError.AWAITING_HUMAN}
    _err(await toolbox.call("request_human", {"reason": "또"}), ToolError.AWAITING_HUMAN)
    assert await gate.answer(gate.pending()[0].id, DONE)
    assert (await waiting).ok
    assert (await toolbox.call("snapshot")).ok


async def test_cancelled_run_leaves_no_pending_task(host, driver, slept):
    gate = InMemoryHumanGate()
    toolbox = _toolbox(host, driver, gate, slept)
    await _open(toolbox, LOGIN)
    waiting = asyncio.create_task(toolbox.call("request_login", {"site": "짐"}))
    await _until_pending(gate)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert gate.pending() == () and toolbox.awaiting_human is None
    await toolbox.close()  # 가드는 이미 내려가 있다 — 다시 부를 것이 없다
