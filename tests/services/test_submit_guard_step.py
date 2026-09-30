"""단계 이동 자동 통과(D17) — 대역 드라이버 위에서 step 창과 사후 확인의 배선.

실제 브라우저·짐 사이트로 같은 것을 보는 것은 test_submit_guard_native.py 다. 여기는 빠르게:
단계 버튼의 폼 제출만 통과·다른 폼 제출은 여전히 막힘·마지막 단계 신호면 strict·결과 화면별
처리(다음 단계 / 그대로 / 완료 → INCIDENT / 애매 → 사람)·페이지 단계 번호.
"""

from pathlib import Path

import pytest

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_effects import Send, Show, SubmitForm
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.click import ClickRisk, ElementDescriptor, RiskyClick
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTaskKind
from auto_apply.domain.submit_guard_policy import BlockReason, GuardMode
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.browser_toolbox_click import STEP_UNCLEAR_REASON
from auto_apply.services.submit_guard import STEP_LANDING_S
from tests.toolbox_kit import FakeDocuments

B = "http://step.test"
FINAL = f"{B}/api/submit"
USER = {"kind": "user"}


def _step_button(name: str, to: str, *extra: object) -> FakeElement:
    return FakeElement(
        "button",
        name,
        tag="button",
        type="submit",
        in_form=True,
        default_button=True,
        on_click=(*extra, SubmitForm("POST", to, by_click=True)),  # type: ignore[arg-type]
    )


def _page(title: str, *elements: FakeElement, progress=()) -> FakeDocument:
    return FakeDocument(
        title=title,
        elements=[FakeElement("heading", title, tag="h1"), *elements],
        progress=progress,
    )


def _sites() -> dict[str, FakeDocument]:
    return {
        # type=submit 3단계 — 1·2단계는 통과, 3단계(검토 페이지·입력칸 없음)의 "다음" 은 최종 제출
        f"{B}/s1": _page("1단계", FakeElement("textbox", "이름"), _step_button("다음", f"{B}/s2")),
        f"{B}/s2": _page(
            "2단계",
            FakeElement("textbox", "경력", tag="textarea"),
            _step_button("저장 후 계속", f"{B}/s3"),
        ),
        f"{B}/s3": _page(
            "3단계", FakeElement("text", "이름: 홍길동", tag="p"), _step_button("다음", FINAL)
        ),
        # 진행 표시가 마지막인데 입력칸도 있다 — 신호는 진행 표시 하나
        f"{B}/p1": _page(
            "지원서",
            FakeElement("textbox", "자기소개"),
            _step_button("다음", FINAL),
            progress=((1, 2), (2, 2)),
        ),
        # 신호 없이 "다음" 으로 최종 제출하는 사이트 — 받아들인 위험, L5 가 멈춘다
        f"{B}/n1": _page(
            "지원서", FakeElement("textbox", "이름"), _step_button("다음", f"{B}/done")
        ),
        f"{B}/done": _page("완료", FakeElement("status", "지원이 완료되었습니다", tag="p")),
        # 입력칸도 완료 문구도 없는 화면으로 간다 — 사람이 본다
        f"{B}/d1": _page("지원서", FakeElement("textbox", "이름"), _step_button("다음", f"{B}/d2")),
        f"{B}/d2": _page("접수", FakeElement("text", "접수 번호 1234", tag="p")),
        # 단계 버튼의 핸들러가 다른 폼을 submit() — step 창은 그 버튼 자신의 제출만 연다
        f"{B}/x1": _page(
            "지원서",
            FakeElement("textbox", "이름"),
            _step_button("Next", f"{B}/s2", SubmitForm("POST", FINAL)),
        ),
        # 단계 버튼 안의 체크박스 — check 는 step 창을 열지 않는다(사후 확인은 click 에만 있다)
        f"{B}/c1": _page(
            "지원서",
            FakeElement(
                "checkbox",
                "다음 단계로 넘어가기",
                type="checkbox",
                ancestors=(
                    ElementDescriptor(tag="button", type="submit", text="다음", in_form=True),
                ),
                on_change=(Send("POST", FINAL),),
            ),
        ),
        # 필수 칸 검증에 걸려 아무 일도 없다
        f"{B}/v1": _page(
            "지원서",
            FakeElement("textbox", "이름"),
            FakeElement(
                "button",
                "다음",
                tag="button",
                type="submit",
                in_form=True,
                on_click=(Show("이름을 입력하세요", role="alert"),),
            ),
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


def _toolbox(host, driver, slept, gate=None) -> BrowserToolbox:
    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return BrowserToolbox(
        host,
        driver,
        FakeDocuments({}),
        human_gate=gate or ScriptedHumanGate(),
        user_id="local",
        application_id="app_s",
        run_id="run_s",
        human_wait_s=1.0,
        sleep=sleep,
    )


@pytest.fixture
def toolbox(host, driver, slept) -> BrowserToolbox:
    return _toolbox(host, driver, slept)


async def _refs(toolbox: BrowserToolbox) -> dict[str, str]:
    snap = (await toolbox.call("snapshot")).snapshot
    assert snap is not None
    return {n.name: n.ref for n in snap.nodes if n.ref}


async def _fill_and_press(toolbox: BrowserToolbox, field: str, button: str) -> ToolResult:
    refs = await _refs(toolbox)
    assert (await toolbox.call("fill", {"ref": refs[field], "value": "홍길동", "source": USER})).ok
    return await toolbox.call("click", {"ref": refs[button]})


async def test_three_step_form_reaches_the_last_step_and_the_final_button_is_blocked(
    toolbox, driver
):
    assert (await toolbox.call("navigate", {"url": f"{B}/s1"})).ok
    first = await _fill_and_press(toolbox, "이름", "다음")
    assert first.ok and "2단계" in first.message, first
    assert driver.mode is GuardMode.RELAXED  # step 창은 끝나면 relaxed 로 되돌린다
    second = await _fill_and_press(toolbox, "경력", "저장 후 계속")
    assert second.ok and "3단계" in second.message, second
    assert driver.sent == [("POST", f"{B}/s2"), ("POST", f"{B}/s3")]

    refs = await _refs(toolbox)
    last = await toolbox.call("click", {"ref": refs["다음"]})  # 검토 페이지 — 입력칸 없음
    assert (last.ok, last.error) == (False, ToolError.SUBMIT_BLOCKED)
    assert ("POST", FINAL) not in driver.sent
    assert (await toolbox.call("ready_for_review", {"submit_ref": refs["다음"]})).ok
    review = toolbox.review
    assert review is not None and review.step == 3
    assert isinstance(review.verdict, RiskyClick) and review.verdict.reason is ClickRisk.LAST_STEP
    assert [e.step for e in review.fill_log.entries] == [1, 2]


async def test_last_step_progress_marker_keeps_the_step_button_strict(toolbox, driver):
    assert (await toolbox.call("navigate", {"url": f"{B}/p1"})).ok
    result = await _fill_and_press(toolbox, "자기소개", "다음")
    assert (result.ok, result.error) == (False, ToolError.SUBMIT_BLOCKED)
    assert [b.reason for b in result.guard.blocked] == [BlockReason.FORM_SUBMIT]
    assert driver.sent == []


async def test_step_without_last_step_signal_that_final_submits_is_an_incident(toolbox, driver):
    # D17 의 받아들인 위험 — 사이트에는 나갔고, L5 가 즉시 멈춘다
    assert (await toolbox.call("navigate", {"url": f"{B}/n1"})).ok
    result = await _fill_and_press(toolbox, "이름", "다음")
    assert result.error is ToolError.INCIDENT
    assert toolbox.incident is not None
    assert (await toolbox.call("snapshot")).error is ToolError.INCIDENT


async def test_step_button_opens_only_its_own_form_submission(toolbox, driver):
    assert (await toolbox.call("navigate", {"url": f"{B}/x1"})).ok
    result = await _fill_and_press(toolbox, "이름", "Next")
    assert (result.ok, result.error) == (False, ToolError.SUBMIT_BLOCKED)
    assert [b.reason for b in result.guard.blocked] == [BlockReason.FORM_SUBMIT]
    assert ("POST", FINAL) not in driver.sent


async def test_step_that_goes_nowhere_keeps_the_step_number(toolbox, driver):
    assert (await toolbox.call("navigate", {"url": f"{B}/v1"})).ok
    result = await _fill_and_press(toolbox, "이름", "다음")
    assert result.ok and "그대로" in result.message
    refs = await _refs(toolbox)
    assert (await toolbox.call("ready_for_review", {"submit_ref": refs["다음"]})).ok
    assert toolbox.review is not None and toolbox.review.step == 1


async def test_unclear_landing_waits_then_hands_over_to_a_human(host, driver, slept):
    gate = ScriptedHumanGate([HumanReply(outcome=HumanOutcome.DONE, note="아직 지원 전이에요")])
    toolbox = _toolbox(host, driver, slept, gate)
    assert (await toolbox.call("navigate", {"url": f"{B}/d1"})).ok
    result = await _fill_and_press(toolbox, "이름", "다음")
    assert result.ok and "아직 지원 전이에요" in result.message, result
    assert sum(s for s in slept if s < 1) == pytest.approx(STEP_LANDING_S)  # 늦게 그리는 SPA 대비
    [task] = gate.asked
    assert (task.kind, task.reason, task.page_url) == (
        HumanTaskKind.INPUT,
        STEP_UNCLEAR_REASON,
        f"{B}/d2",
    )
    assert driver.armed  # 재개 뒤 가드는 다시 섰다


async def test_unclear_landing_ends_the_run_when_nobody_answers(host, driver, slept):
    gate = ScriptedHumanGate([HumanReply(outcome=HumanOutcome.TIMED_OUT)])
    toolbox = _toolbox(host, driver, slept, gate)
    assert (await toolbox.call("navigate", {"url": f"{B}/d1"})).ok
    result = await _fill_and_press(toolbox, "이름", "다음")
    assert result.error is ToolError.NEEDS_INPUT
    assert toolbox.needs_human is not None
    assert (await toolbox.call("snapshot")).error is ToolError.RUN_FINISHED


async def test_step_window_waits_out_the_strict_tail(host, toolbox, driver, slept):
    # strict 꼬리 동안 문서 탐색을 여는 창을 열면 risky 클릭이 미룬 제출이 섞여 나간다
    assert (await toolbox.call("navigate", {"url": f"{B}/s1"})).ok
    later = FakeElement("button", "보내기 예약", tag="button", type="button")
    driver.document(await host.page()).elements.append(later)
    refs = await _refs(toolbox)
    assert (await toolbox.call("fill", {"ref": refs["이름"], "value": "홍길동", "source": USER})).ok
    assert (await toolbox.call("click", {"ref": refs["보내기 예약"]})).ok  # strict 창
    slept.clear()
    refs = await _refs(toolbox)
    assert (await toolbox.call("click", {"ref": refs["다음"]})).ok  # step 창
    assert slept and slept[0] > 0  # step 창도 relaxed 처럼 꼬리가 지난 뒤 연다
    assert driver.sent == [("POST", f"{B}/s2")]


async def test_check_never_opens_a_step_window(toolbox, driver):
    assert (await toolbox.call("navigate", {"url": f"{B}/c1"})).ok
    refs = await _refs(toolbox)
    args = {"ref": refs["다음 단계로 넘어가기"], "on": True, "source": USER}
    result = await toolbox.call("check", args)
    assert (result.ok, result.error) == (False, ToolError.SUBMIT_BLOCKED)
    assert [b.reason for b in result.guard.blocked] == [BlockReason.STRICT_NON_GET]
    assert driver.sent == []
