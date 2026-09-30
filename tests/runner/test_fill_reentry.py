"""ask_user 타임아웃 → NEEDS_INPUT → 늦은 답 → 재진입 run → AWAITING_APPROVAL (§A5, D8·D10).

재진입 run 은 같은 URL 을 다시 열고 직전 FillLog 와 받은 답을 프롬프트로 받는다.
가린 답(sensitive)은 DB·job payload·run 기록·로그·transcript(도구 결과·프롬프트) 어디에도
없어야 한다(절대 규칙 5).
"""

import pytest
from structlog.testing import capture_logs

from auto_apply.adapters.agent.scripted import ScriptedCall
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTaskKind
from auto_apply.contracts.jobs import JobKind, JobStatus
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import InvalidInput, NotFound
from tests.runner.fill_kit import FORM, PROFILE, FillRig, ref, scripted, step

SECRET = "장애 3급 청각"
Q = "희망 연봉"


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path)
    rig.human_wait_s = 0.01  # 아무도 답하지 않는다 → 타임아웃
    yield rig
    await rig.host.close()


def _first_run(*, sensitive: bool = False):
    return scripted(
        [
            ScriptedCall("navigate", {"url": FORM}),
            ScriptedCall("snapshot"),
            step("fill", ref=ref("이름"), value="홍길동", source=PROFILE),
            ScriptedCall("ask_user", {"question": Q, "sensitive": sensitive}),
        ]
    )


def _second_run(source: dict[str, object], value: str):
    return scripted(
        [
            ScriptedCall("navigate", {"url": FORM}),
            ScriptedCall("snapshot"),
            step("fill", ref=ref("이름"), value=value, source=source),
            step("ready_for_review", submit_ref=ref("지원서 제출")),
        ]
    )


async def _timed_out(rig: FillRig, *, sensitive: bool = False) -> tuple[str, str]:
    app, job = await rig.queued()
    await rig.handler(_first_run(sensitive=sensitive))(job)
    assert await rig.apps.current_state(app) is S.NEEDS_INPUT
    run = await rig.run_record(app)
    task = await rig.artifacts.load_human_task(run.run_id)
    assert task is not None and (task.kind, task.question) == (HumanTaskKind.QUESTION, Q)
    return app, run.run_id


async def test_late_answer_resumes_in_a_new_run_up_to_approval(rig):
    app, run1 = await _timed_out(rig)
    runner = rig.runner({})
    job = await rig.reentry(runner.enqueue).answer(run1, "5000만 원")
    assert (job.kind, job.application_id, job.payload["resume_from"]) == (JobKind.FILL, app, run1)
    kb = await rig.profiles.list_answers("local")
    assert [(a.question_key, a.answer) for a in kb] == [(Q, "5000만 원")]
    source = job.payload["answer"]
    assert source == {"kind": "answer_kb", "key": kb[0].id}

    runtime = _second_run(source, "5000만 원")
    await rig.handler(runtime)(job)
    assert await rig.apps.current_state(app) is S.AWAITING_APPROVAL
    prompt = runtime.seen_prompt
    assert "[이어서 — 직전 run]" in prompt and f"{kb[0].id}: {Q} → 5000만 원" in prompt
    assert '이름(textbox) fill ← "홍길동" (source {kind: profile, key: name})' in prompt
    assert f'받은 답: "{Q}" → 5000만 원' in prompt
    run2 = next(h.run_id for h in reversed(await rig.history(app)) if h.run_id)
    review = await rig.artifacts.load_review(run2)
    assert review is not None
    assert [e.value for e in review.fill_log.entries] == ["5000만 원"]  # 새 run 의 새 기록
    assert rig.final_submits() == []


async def test_hidden_late_answer_leaves_no_trace(rig):
    with capture_logs() as logs:
        app, run1 = await _timed_out(rig, sensitive=True)
        job = await rig.reentry(rig.runner({}).enqueue).answer(run1, SECRET)
        assert job.payload["answer"]["kind"] == "user"
        runtime = _second_run(job.payload["answer"], "")
        await rig.handler(runtime)(job)
    assert await rig.apps.current_state(app) is S.AWAITING_APPROVAL
    doc = rig.driver.document(await rig.host.page())
    assert next(e.value for e in doc.elements if e.name == "이름") == SECRET  # 앱이 채웠다
    assert rig.held.get(app) == {}  # 승인 대기로 갔으면 메모리에서도 버린다
    blobs = [await rig.store.get(k) for k in await rig.store.list_keys("runs/")]
    traces = [
        repr(rig.db),
        runtime.seen_prompt,
        *(r.content for r in runtime.replies),
        *(repr(e) for e in logs),
        *(b.decode() for b in blobs),
    ]
    assert all(SECRET not in t for t in traces)
    assert await rig.profiles.list_answers("local") == []
    review = await rig.artifacts.load_review(
        next(h.run_id for h in reversed(await rig.history(app)) if h.run_id)
    )
    assert review is not None and review.fill_log.entries[-1].withheld


async def test_hidden_answer_lost_on_restart_is_asked_again(rig):
    app, run1 = await _timed_out(rig, sensitive=True)
    job = await rig.reentry(rig.runner({}).enqueue).answer(run1, SECRET)
    rig.held.drop(app)  # 앱 재시작 흉내 — 메모리가 비었다
    runtime = _second_run(job.payload["answer"], "")
    await rig.handler(runtime)(job)
    assert "ask_user 로 다시 묻는다" in runtime.seen_prompt
    fill = next(r for r in runtime.replies if '"tool":"fill"' in r.content)
    assert not fill.ok  # 빈 값으로 칸을 덮지 않는다


async def test_answer_in_time_resumes_the_same_run(rig):
    rig.gate = type(rig.gate)([HumanReply(outcome=HumanOutcome.DONE, answer="4000")])
    runtime = scripted(
        [
            ScriptedCall("navigate", {"url": FORM}),
            ScriptedCall("snapshot"),
            ScriptedCall("ask_user", {"question": Q}),
            step("fill", ref=ref("이름"), value="4000", source=PROFILE),
            step("ready_for_review", submit_ref=ref("지원서 제출")),
        ]
    )
    app, job = await rig.queued()
    await rig.handler(runtime)(job)
    assert await rig.apps.current_state(app) is S.AWAITING_APPROVAL
    assert '"answer":{"source":{"kind":"answer_kb"' in runtime.replies[2].content


async def test_answer_is_refused_unless_the_application_waits_for_it(rig):
    _, run1 = await _timed_out(rig)
    reentry = rig.reentry(rig.runner({}).enqueue)
    with pytest.raises(InvalidInput):
        await reentry.answer(run1, "   ")
    await reentry.answer(run1, "5000")
    with pytest.raises(InvalidInput):  # 이미 이어서 돌 fill 이 대기 중
        await reentry.answer(run1, "6000")
    assert len(await rig.jobs(JobStatus.QUEUED)) == 1
    with pytest.raises(NotFound):
        await reentry.answer("run_unknown", "5000")


async def test_answer_needs_needs_input_state(rig):
    app, run1 = await _timed_out(rig)
    await rig.apps.transition(app, S.QUEUED, run_id=None)
    with pytest.raises(InvalidInput):
        await rig.reentry(rig.runner({}).enqueue).answer(run1, "5000")


@pytest.mark.parametrize(
    "payload",
    [{"resume_from": 3}, {"resume_from": "run_1", "answer": {"kind": "answer_kb"}}],
    ids=["run-id", "source"],
)
async def test_bad_resume_payload_is_refused_before_the_run(rig, payload):
    app, job = await rig.queued()
    with pytest.raises(InvalidInput):
        await rig.handler(scripted([]))(job.model_copy(update={"payload": payload}))
    assert await rig.apps.current_state(app) is S.QUEUED
