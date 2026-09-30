"""ask_user — 실행 중 질문 → 답 → 재개 (§A5, D10, 절대 규칙 5). 대역 드라이버·사람 대역 위에서.

핵심 경계: 보통 답은 답변 KB 에 남고 에이전트가 값을 받는다. 가린 답(sensitive·주민번호 꼴)은
KB·FillLog·도구 결과·로그 어디에도 없고, 앱이 핸들로 칸을 채운다.
"""

import asyncio
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.adapters.repository.memory import InMemoryDatabase, InMemoryUnitOfWork
from auto_apply.contracts.browser_tools import ToolError, ToolResult
from auto_apply.contracts.fill_log import FillSourceKind
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTaskKind
from auto_apply.ports.human_gate import HumanGate
from auto_apply.services.browser_toolbox import BrowserToolbox
from auto_apply.services.browser_toolbox_redact import HIDDEN_VALUE
from auto_apply.services.profile import ProfileService
from tests.services.fakes import FixedClock, SeqIds
from tests.toolbox_kit import FakeDocuments

B = "http://site.test"
FORM = f"{B}/apply"
SECRET = "장애 3급 청각"
RRN = "900101-1234567"
Q = "장애 여부를 알려 주세요"


def _sites() -> dict[str, FakeDocument]:
    return {
        FORM: FakeDocument(
            title="지원서",
            elements=[
                FakeElement("textbox", "희망 연봉", in_form=True),
                FakeElement("textbox", "장애 사항", in_form=True),
                FakeElement("combobox", "장애 등급", tag="select", options=("없음", "3급")),
            ],
        )
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
def profiles() -> ProfileService:
    db = InMemoryDatabase()
    return ProfileService(lambda: InMemoryUnitOfWork(db), FixedClock(), SeqIds())


def _toolbox(host, driver, gate: HumanGate, answers, wait_s: float = 5) -> BrowserToolbox:
    return BrowserToolbox(
        host, driver, FakeDocuments({}), human_gate=gate, human_wait_s=wait_s, answers=answers,
        user_id="local", application_id="app_q", run_id="run_q",
    )  # fmt: skip


async def _open(toolbox: BrowserToolbox) -> dict[str, str]:
    assert (await toolbox.call("navigate", {"url": FORM})).ok
    snap = await toolbox.call("snapshot")
    assert snap.snapshot is not None
    return {n.name: n.ref for n in snap.snapshot.nodes if n.ref}


def _answered(text: str) -> HumanReply:
    return HumanReply(outcome=HumanOutcome.DONE, answer=text)


async def _dom(driver, host, name: str) -> str:
    doc = driver.document(await host.page())
    return next(e.value for e in doc.elements if e.name == name)


async def test_answer_is_saved_to_kb_and_fills_with_its_source(host, driver, profiles):
    gate = ScriptedHumanGate([_answered("5000만 원")])
    toolbox = _toolbox(host, driver, gate, profiles)
    refs = await _open(toolbox)
    asked = await toolbox.call(
        "ask_user", {"question": "희망 연봉은?*", "field_hint": "숫자", "options": None}
    )
    assert asked.ok and asked.answer is not None
    assert asked.answer.value == "5000만 원"
    assert asked.answer.source.kind is FillSourceKind.ANSWER_KB
    task = gate.asked[0]
    assert (task.kind, task.question, task.page_url) == (
        HumanTaskKind.QUESTION,
        "희망 연봉은?*",
        FORM,
    )
    kb = await profiles.list_answers("local")
    assert [(a.id, a.question_key, a.answer, a.source_application_id) for a in kb] == [
        (asked.answer.source.key, "희망 연봉은", "5000만 원", "app_q")
    ]
    source = asked.answer.source.model_dump(mode="json")
    fill = await toolbox.call(
        "fill", {"ref": refs["희망 연봉"], "value": "5000만 원", "source": source}
    )
    assert fill.ok
    entry = toolbox.fill_log.entries[-1]
    assert (entry.value, entry.source, entry.withheld) == ("5000만 원", asked.answer.source, False)


async def test_same_question_updates_the_kb_row(host, driver, profiles):
    gate = ScriptedHumanGate([_answered("4000"), _answered("4500")])
    toolbox = _toolbox(host, driver, gate, profiles)
    await _open(toolbox)
    first = await toolbox.call("ask_user", {"question": "희망 연봉"})
    second = await toolbox.call("ask_user", {"question": "  희망  연봉?"})
    assert first.answer is not None and second.answer is not None
    assert first.answer.source == second.answer.source
    assert [a.answer for a in await profiles.list_answers("local")] == ["4500"]


async def test_tools_are_refused_while_waiting_and_resume_after_answer(host, driver, profiles):
    gate = InMemoryHumanGate()
    toolbox = _toolbox(host, driver, gate, profiles)
    await _open(toolbox)
    waiting = asyncio.create_task(toolbox.call("ask_user", {"question": Q}))
    for _ in range(1000):
        if gate.pending():
            break
        await asyncio.sleep(0)
    assert toolbox.awaiting_human is not None
    assert (await toolbox.call("snapshot")).error is ToolError.AWAITING_HUMAN
    assert await gate.answer(gate.pending()[0].id, _answered("없음"))
    assert (await waiting).ok
    assert (await toolbox.call("snapshot")).ok


async def test_timeout_ends_the_run_as_needs_input(host, driver, profiles):
    toolbox = _toolbox(host, driver, ScriptedHumanGate(), profiles, wait_s=0.01)
    await _open(toolbox)
    result = await toolbox.call("ask_user", {"question": Q, "options": ["예", "아니오"]})
    assert (result.ok, result.error) == (False, ToolError.NEEDS_INPUT)
    assert toolbox.needs_human is not None
    assert (toolbox.needs_human.kind, toolbox.needs_human.options) == (
        HumanTaskKind.QUESTION,
        ("예", "아니오"),
    )
    assert (await toolbox.call("snapshot")).error is ToolError.RUN_FINISHED
    assert await profiles.list_answers("local") == []


@pytest.mark.parametrize(
    "reply",
    [HumanReply(outcome=HumanOutcome.DECLINED), _answered("   ")],
    ids=["declined", "blank"],
)
async def test_declined_answer_lets_the_run_continue(host, driver, profiles, reply):
    toolbox = _toolbox(host, driver, ScriptedHumanGate([reply]), profiles)
    await _open(toolbox)
    result = await toolbox.call("ask_user", {"question": Q})
    assert (result.ok, result.error) == (False, ToolError.ANSWER_DECLINED)
    assert toolbox.needs_human is None
    assert (await toolbox.call("snapshot")).ok
    assert await profiles.list_answers("local") == []


@pytest.mark.parametrize(
    ("sensitive", "answer"),
    [(True, SECRET), (False, f"번호는 {RRN}")],
    ids=["sensitive", "resident-number-shaped"],
)
async def test_hidden_answer_never_reaches_kb_log_agent_or_fill_log(
    host, driver, profiles, sensitive, answer
):
    gate = ScriptedHumanGate([_answered(answer)])
    toolbox = _toolbox(host, driver, gate, profiles)
    refs = await _open(toolbox)
    results: list[ToolResult] = []
    with capture_logs() as logs:
        asked = await toolbox.call("ask_user", {"question": Q, "sensitive": sensitive})
        assert asked.ok and asked.answer is not None and asked.answer.value is None
        assert asked.answer.source.kind is FillSourceKind.USER
        source = asked.answer.source.model_dump(mode="json")
        results.append(asked)
        results.append(
            await toolbox.call("fill", {"ref": refs["장애 사항"], "value": "", "source": source})
        )
        results.append(await toolbox.call("snapshot"))
        assert await _dom(driver, host, "장애 사항") == answer  # 앱이 채웠다
    assert all(r.ok for r in results)
    entry = toolbox.fill_log.entries[-1]
    assert (entry.withheld, entry.value, entry.source) == (True, None, asked.answer.source)
    snap = results[-1].snapshot
    assert snap is not None
    assert next(n.value for n in snap.nodes if n.name == "장애 사항") == HIDDEN_VALUE
    visible = [r.model_dump_json() for r in results] + [toolbox.fill_log.model_dump_json()]
    visible += [repr(e) for e in logs] + [repr(gate.asked)]
    for text in visible:
        assert answer not in text and RRN not in text
    assert await profiles.list_answers("local") == []


async def test_hidden_answer_can_pick_a_select_option(host, driver, profiles):
    gate = ScriptedHumanGate([_answered("3급")])
    toolbox = _toolbox(host, driver, gate, profiles)
    refs = await _open(toolbox)
    asked = await toolbox.call("ask_user", {"question": "장애 등급", "sensitive": True})
    assert asked.answer is not None
    source = asked.answer.source.model_dump(mode="json")
    chosen = await toolbox.call(
        "select", {"ref": refs["장애 등급"], "option": "", "source": source}
    )
    assert chosen.ok and "3급" not in chosen.model_dump_json()
    assert await _dom(driver, host, "장애 등급") == "3급"
    assert toolbox.fill_log.entries[-1].withheld


async def test_empty_option_needs_a_hidden_answer(host, driver, profiles):
    toolbox = _toolbox(host, driver, ScriptedHumanGate(), profiles)
    refs = await _open(toolbox)
    as_profile = {"ref": refs["장애 등급"], "option": "", "source": {"kind": "profile", "key": "x"}}
    assert (await toolbox.call("select", as_profile)).error is ToolError.INVALID_INPUT
    unknown = {"kind": "user", "key": "no-such-handle"}
    empty = {"ref": refs["장애 등급"], "option": "", "source": unknown}
    assert (await toolbox.call("select", empty)).error is ToolError.INVALID_INPUT


async def test_hidden_answer_in_a_url_is_refused(host, driver, profiles):
    toolbox = _toolbox(host, driver, ScriptedHumanGate([_answered("비밀스러운답")]), profiles)
    await _open(toolbox)
    assert (await toolbox.call("ask_user", {"question": Q, "sensitive": True})).ok
    nav = await toolbox.call("navigate", {"url": f"{B}/next?memo=비밀스러운답"})
    assert nav.error is ToolError.FORBIDDEN_URL


async def test_kb_failure_still_hands_the_answer_to_this_run(host, driver):
    class Broken:
        async def remember_answer(self, *a, **kw):
            raise RuntimeError("db down")

    toolbox = _toolbox(host, driver, ScriptedHumanGate([_answered("가능")]), Broken())
    await _open(toolbox)
    asked = await toolbox.call("ask_user", {"question": "입사 가능일"})
    assert asked.ok and asked.answer is not None
    assert (asked.answer.value, asked.answer.source.kind) == ("가능", FillSourceKind.USER)


async def test_question_without_letters_is_refused(host, driver, profiles):
    toolbox = _toolbox(host, driver, ScriptedHumanGate(), profiles)
    result = await toolbox.call("ask_user", {"question": "???"})
    assert result.error is ToolError.INVALID_INPUT
    assert toolbox.needs_human is None


async def test_held_answers_from_a_previous_run_fill_by_handle(host, driver, profiles):
    toolbox = BrowserToolbox(
        host, driver, FakeDocuments({}), human_gate=ScriptedHumanGate(), answers=profiles,
        held_answers={"h1": SECRET}, user_id="local", application_id="app_q", run_id="run_2",
    )  # fmt: skip
    refs = await _open(toolbox)
    source = {"kind": "user", "key": "h1"}
    assert (
        await toolbox.call("fill", {"ref": refs["장애 사항"], "value": "", "source": source})
    ).ok
    assert await _dom(driver, host, "장애 사항") == SECRET
    assert toolbox.fill_log.entries[-1].withheld
