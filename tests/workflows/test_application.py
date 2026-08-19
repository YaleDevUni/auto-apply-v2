"""ApplicationWorkflow 테스트 (ARCHITECTURE.md §2.2).

시간 건너뛰기(time-skipping) 환경을 쓰므로 72시간 승인 타임아웃과 30일 뒤 예약도 즉시 검증된다.
이게 durable timer 를 테스트로 증명할 수 있는 이유다.
"""

from datetime import timedelta

import pytest
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.contracts.dto import (
    ApproveSignal,
    GuidePatchDecisionSignal,
    GuidePatchReviseSignal,
    RejectSignal,
    RescheduleSignal,
    ReviseSignal,
    StartApplication,
)
from auto_apply.domain.enums import ApplicationState, AttemptOutcome, ExecutionMode, RevisionScope
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_BROWSER, QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.resume import ResumeWorkflow
from tests.conftest import JOB_URL, Harness

pytestmark = pytest.mark.integration

APP_ID = "app_1"


def _cmd(**kw: object) -> StartApplication:
    base: dict[str, object] = {
        "application_id": APP_ID,
        "user_id": "u1",
        "job_url": JOB_URL,
        "dry_run_only": True,
    }
    base.update(kw)
    return StartApplication.model_validate(base)


async def _start(
    client: Client, cmd: StartApplication
) -> WorkflowHandle[ApplicationWorkflow, object]:
    return await client.start_workflow(
        ApplicationWorkflow.run,
        cmd,
        id=f"application-{cmd.application_id}",
        task_queue=QUEUE_DEFAULT,
    )


class _Workers:
    """세 큐에 각각 워커를 띄운다 — 큐 라우팅이 실제로 맞는지도 같이 검증된다 (§1)."""

    def __init__(self, client: Client, harness: Harness) -> None:
        self._client = client
        self._acts = harness.activities()
        self._workers: list[Worker] = []

    async def __aenter__(self) -> "_Workers":
        specs = [
            (QUEUE_DEFAULT, [ApplicationWorkflow]),
            (QUEUE_AI, [ResumeWorkflow]),
            (QUEUE_BROWSER, []),
        ]
        for queue, wfs in specs:
            # max_cached_workflows=0 → sticky execution 비활성화.
            # 워커가 죽어도 서버가 죽은 워커의 sticky 큐로 라우팅해서 재시작 테스트가 멈춘다.
            w = Worker(
                self._client,
                task_queue=queue,
                workflows=wfs,
                activities=self._acts,
                max_cached_workflows=0,
            )
            await w.__aenter__()
            self._workers.append(w)
        return self

    async def __aexit__(self, *exc: object) -> None:
        for w in reversed(self._workers):
            await w.__aexit__(None, None, None)  # type: ignore[arg-type]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        yield env


# ─────────────────────────── 승인 경로 ───────────────────────────
async def test_approve_then_scheduled_execution_completes(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

        run_at = (await env.get_current_time()) + timedelta(days=30)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=run_at))

        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert "dry_run" in result.reason
    # 상태 전이가 projection 으로 남아야 한다 (§4.1)
    assert h.states(APP_ID) == [
        "evaluating",
        "generating_resume",
        "rendering_pdf",
        "awaiting_approval",
        "scheduled",
        "executing",
        "completed",
    ]
    # 실행 1회 = application_attempts 1행 (§4)
    attempt = h.attempt(APP_ID, 1)
    assert attempt is not None
    assert attempt.mode is ExecutionMode.DRY_RUN
    assert attempt.outcome is AttemptOutcome.SUCCEEDED
    assert attempt.recipe_platform == "fixture"


async def test_reject_signal_ends_as_rejected(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.reject, RejectSignal(reason="회사 부적합"))
        result = await handle.result()

    assert result.state is ApplicationState.REJECTED
    assert result.reason == "회사 부적합"
    assert "executing" not in h.states(APP_ID), "거절했는데 실행 단계로 갔다"


async def test_duplicate_approval_is_idempotent(env: WorkflowEnvironment):
    """Telegram 버튼은 두 번 눌린다. 첫 승인만 반영되어야 한다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        first = (await env.get_current_time()) + timedelta(days=10)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=first))
        # 두 번째 승인은 무시되어야 한다 (거절로 뒤집히지도 않아야 한다)
        await handle.signal(ApplicationWorkflow.reject, RejectSignal(reason="늦은 거절"))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED


async def test_approve_with_wrong_nonce_is_ignored(env: WorkflowEnvironment):
    """회귀 테스트: nonce 검증은 워크플로우 안에서 한다(ports/notifier.py 참고) — 어댑터

    메모리에 두면 발급 프로세스(worker)와 검증 프로세스(webhook/listener)가 갈라질 때
    항상 실패한다(라이브 스모크테스트로 실측). 가짜/오래된 nonce 는 조용히 무시되고,
    올바른 nonce 는 그대로 통과해야 한다.
    """
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce="not-the-real-nonce"))
        # 위조 nonce 는 무시됐어야 한다 — 여전히 승인 대기 상태
        await _tick()
        assert (await handle.query(ApplicationWorkflow.state)).state is (
            ApplicationState.AWAITING_APPROVAL
        )

        assert h.notifier is not None
        real_nonce = None
        for _ in range(100):
            real_nonce = h.notifier.last_ticket.get(APP_ID)
            if real_nonce is not None:
                break
            await _tick()
        assert real_nonce is not None, "request_approval activity 가 끝나지 않았다"
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=real_nonce))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED


# ─────────────────────────── REVISE(수정요청) ───────────────────────────
# `applications.status`(projection)는 §4.1 대로 (workflow_run_id, state) 로 멱등 upsert 된다
# (adapters/repository/memory.py) — REVISE 로 같은 state(generating_resume 등)를 같은
# 워크플로우 실행 안에서 다시 지나가도 새 행이 아니라 기존 행이 덮어써진다(예약 재조정과
# 같은 이유, tests_reschedule 참고). 그래서 "라운드가 실제로 돌았다"는 신호는 상태 개수가
# 아니라 매 라운드 새로 발급되는 decision nonce 로 확인한다.
async def _wait_new_nonce(h: Harness, seen: set[str]) -> str:
    """`h.notifier.last_ticket`에 `seen`에 없는 nonce 가 뜰 때까지 기다린다.

    `_wait_state(AWAITING_APPROVAL)`는 `self._state`가 바뀌는 순간(= request_approval activity
    호출 *전*)에 이미 반환되므로, 첫 nonce 조차 이 폴링 없이는 아직 안 채워져 있을 수 있다
    (`test_approve_with_wrong_nonce_is_ignored`와 같은 이유).
    """
    assert h.notifier is not None
    for _ in range(300):
        candidate = h.notifier.last_ticket.get(APP_ID)
        if candidate is not None and candidate not in seen:
            return candidate
        await _tick()
    raise AssertionError(f"새 nonce 가 발급되지 않았다 (seen={seen})")


async def test_revise_specific_regenerates_and_reapproves(env: WorkflowEnvironment):
    """REVISE(specific) — 재생성 후 다시 승인 대기로 돌아온다. 피드백은 이번 라운드에만 쓰인다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        first_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="자기소개를 더 짧게", scope=RevisionScope.SPECIFIC),
        )
        second_nonce = await _wait_new_nonce(h, {first_nonce})
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=second_nonce))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED


async def test_revise_with_stale_nonce_from_previous_round_is_ignored(env: WorkflowEnvironment):
    """라운드마다 새 nonce 가 발급된다 — 이전 라운드 nonce 로 보낸 승인은 무시돼야 한다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        first_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="더 짧게", scope=RevisionScope.SPECIFIC),
        )
        second_nonce = await _wait_new_nonce(h, {first_nonce})

        # 1라운드 nonce 로 보낸 승인은 무시돼야 한다 — 여전히 대기 상태여야 한다
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=first_nonce))
        await _tick()
        state = await handle.query(ApplicationWorkflow.state)
        assert state.state is ApplicationState.AWAITING_APPROVAL

        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=second_nonce))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED


async def test_revise_general_applies_guide_patch_after_second_approval(env: WorkflowEnvironment):
    """REVISE(general) — 가이드 patch 는 사람이 diff 를 한 번 더 승인해야 반영된다."""
    h = Harness(
        guide_patch_payloads=[
            {"patches": [{"old": "", "new": "항상 존댓말로 쓴다.", "rationale": "사용자 요청"}]}
        ]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        main_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="항상 존댓말로 써줘", scope=RevisionScope.GENERAL),
        )

        # 가이드 patch 2차 승인 요청의 nonce 가 나올 때까지 기다린다 (본 승인 nonce 와 다르다)
        guide_nonce = await _wait_new_nonce(h, {main_nonce})

        await handle.signal(
            ApplicationWorkflow.approve_guide_patch, GuidePatchDecisionSignal(nonce=guide_nonce)
        )
        # 가이드 반영 → 재생성 → 다시 본 승인 요청, 세 번째 nonce 가 발급된다
        round2_nonce = await _wait_new_nonce(h, {main_nonce, guide_nonce})
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=round2_nonce))
        result = await handle.result()

        assert h.guide is not None
        assert await h.guide.get("fixture") == "항상 존댓말로 쓴다."

    assert result.state is ApplicationState.COMPLETED


async def test_revise_general_applies_all_patches_from_one_multi_instruction_feedback(
    env: WorkflowEnvironment,
):
    """한 REVISE(general) 피드백에 서로 다른 지시가 여러 개 섞여 있으면 모두 반영돼야 한다

    (메모리 resume-revise-feedback-design — old/new 단일 쌍만 표현하던 스키마에서는 다지시
    피드백 중 일부가 조용히 누락됐다, 라이브 테스트로 실측).
    """
    h = Harness(
        guide_patch_payloads=[
            {
                "patches": [
                    {"old": "", "new": "항상 존댓말로 쓴다.", "rationale": "지시 1"},
                    {"old": "", "new": "프로젝트는 3~4개만 싣는다.", "rationale": "지시 2"},
                ]
            }
        ]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        main_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="정중체로 쓰고 프로젝트도 줄여줘", scope=RevisionScope.GENERAL),
        )
        guide_nonce = await _wait_new_nonce(h, {main_nonce})

        await handle.signal(
            ApplicationWorkflow.approve_guide_patch, GuidePatchDecisionSignal(nonce=guide_nonce)
        )
        round2_nonce = await _wait_new_nonce(h, {main_nonce, guide_nonce})
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=round2_nonce))
        result = await handle.result()

        assert h.guide is not None
        assert await h.guide.get("fixture") == ("항상 존댓말로 쓴다.\n\n프로젝트는 3~4개만 싣는다.")

    assert result.state is ApplicationState.COMPLETED


async def test_revise_general_rejected_guide_patch_still_regenerates(env: WorkflowEnvironment):
    """가이드 patch 를 거절해도 이번 라운드 재생성엔 feedback 이 반영된다 — 가이드만 안 바뀐다."""
    h = Harness(
        guide_patch_payloads=[{"patches": [{"old": "", "new": "새 규칙", "rationale": "요청"}]}]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        main_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="이번만 짧게", scope=RevisionScope.GENERAL),
        )
        guide_nonce = await _wait_new_nonce(h, {main_nonce})

        await handle.signal(
            ApplicationWorkflow.reject_guide_patch, GuidePatchDecisionSignal(nonce=guide_nonce)
        )
        round2_nonce = await _wait_new_nonce(h, {main_nonce, guide_nonce})
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=round2_nonce))
        result = await handle.result()

        assert h.guide is not None
        assert await h.guide.get("fixture") == ""  # 거절했으니 가이드는 그대로

    assert result.state is ApplicationState.COMPLETED


async def test_guide_patch_revise_regenerates_proposal_then_approves(env: WorkflowEnvironment):
    """가이드 patch 💬 코멘트 — 제안 자체를 다시 받은 뒤 승인하면 코멘트가 반영된 두 번째

    제안이 적용된다(첫 제안이 아니라).
    """
    h = Harness(
        guide_patch_payloads=[
            {"patches": [{"old": "", "new": "존댓말로 쓴다.", "rationale": "1차 제안"}]},
            {
                "patches": [
                    {"old": "", "new": "항상 존댓말로 정중하게 쓴다.", "rationale": "코멘트 반영"}
                ]
            },
        ]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        main_nonce = await _wait_new_nonce(h, set())

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="항상 존댓말로 써줘", scope=RevisionScope.GENERAL),
        )
        guide_nonce = await _wait_new_nonce(h, {main_nonce})

        await handle.signal(
            ApplicationWorkflow.revise_guide_patch,
            GuidePatchReviseSignal(feedback="더 정중하게 다듬어줘", nonce=guide_nonce),
        )
        # 코멘트를 반영한 두 번째 제안이 새 nonce 로 다시 온다
        guide_nonce_2 = await _wait_new_nonce(h, {main_nonce, guide_nonce})

        await handle.signal(
            ApplicationWorkflow.approve_guide_patch, GuidePatchDecisionSignal(nonce=guide_nonce_2)
        )
        round2_nonce = await _wait_new_nonce(h, {main_nonce, guide_nonce, guide_nonce_2})
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=round2_nonce))
        result = await handle.result()

        assert h.guide is not None
        assert await h.guide.get("fixture") == "항상 존댓말로 정중하게 쓴다."

    assert result.state is ApplicationState.COMPLETED


async def test_guide_patch_revise_exceeding_max_rounds_gives_up_guide_but_keeps_going(
    env: WorkflowEnvironment,
):
    """가이드 patch 코멘트도 무한 재제안 루프를 만들지 않는다 — `max_guide_revisions`(설정으로

    조정 가능, config.py)를 넘으면 가이드 반영은 포기하지만, 본 라운드(REVISE) 재생성은 원래
    feedback 그대로 계속된다(revise_guide 실패/거절과 같은 처리 — 가이드 반영은 덤이지 필수
    경로가 아니다). 테스트는 운영 기본값과 무관하게 작게 override 해서 경계만 본다.
    """
    max_guide_revisions = 2
    h = Harness(
        guide_patch_payloads=[
            {"patches": [{"old": "", "new": "제안 1", "rationale": "r1"}]},
            {"patches": [{"old": "", "new": "제안 2", "rationale": "r2"}]},
            {"patches": [{"old": "", "new": "제안 3", "rationale": "r3"}]},
        ]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(max_guide_revisions=max_guide_revisions))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        seen = {await _wait_new_nonce(h, set())}

        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="항상 존댓말로 써줘", scope=RevisionScope.GENERAL),
        )
        guide_nonce = await _wait_new_nonce(h, seen)
        seen.add(guide_nonce)

        for i in range(max_guide_revisions):
            await handle.signal(
                ApplicationWorkflow.revise_guide_patch,
                GuidePatchReviseSignal(feedback=f"코멘트 {i}", nonce=guide_nonce),
            )
            guide_nonce = await _wait_new_nonce(h, seen)
            seen.add(guide_nonce)

        # 이번이 MAX_GUIDE_REVISIONS 를 넘는 마지막 코멘트다 — 가이드 재제안은 더 없고,
        # 본 라운드 재생성으로 넘어가 다시 본 승인 요청(새 nonce)이 온다
        await handle.signal(
            ApplicationWorkflow.revise_guide_patch,
            GuidePatchReviseSignal(feedback="마지막 코멘트", nonce=guide_nonce),
        )
        round2_nonce = await _wait_new_nonce(h, seen)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(nonce=round2_nonce))
        result = await handle.result()

        assert h.guide is not None
        assert await h.guide.get("fixture") == ""  # 가이드 patch 는 결국 반영되지 않았다

    assert result.state is ApplicationState.COMPLETED


async def test_revise_exceeding_max_rounds_goes_needs_human(env: WorkflowEnvironment):
    """무한 재생성 루프를 만들지 않는다 — `max_revisions`(설정으로 조정 가능, config.py)를

    넘으면 사람에게 넘긴다. 테스트는 운영 기본값과 무관하게 작게 override 해서 경계만 본다.
    """
    max_revisions = 2
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(max_revisions=max_revisions))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        assert h.notifier is not None
        seen = {await _wait_new_nonce(h, set())}

        for i in range(max_revisions):
            await handle.signal(
                ApplicationWorkflow.revise,
                ReviseSignal(feedback=f"피드백 {i}", scope=RevisionScope.SPECIFIC),
            )
            seen.add(await _wait_new_nonce(h, seen))

        # 이번이 MAX_REVISIONS 를 넘는 마지막 REVISE 다 — 더는 재승인 요청이 오지 않는다
        await handle.signal(
            ApplicationWorkflow.revise,
            ReviseSignal(feedback="마지막", scope=RevisionScope.SPECIFIC),
        )
        result = await handle.result()

    assert result.state is ApplicationState.NEEDS_HUMAN
    assert "수정요청" in result.reason
    # 회귀 테스트: 여기서 조용히 끝나면 텔레그램을 안 보는 한 아무도 모른다(라이브 세션 실측).
    assert h.notifier is not None
    needs_human_events = [e for e in h.notifier.notified if e.kind == "NEEDS_HUMAN"]
    assert len(needs_human_events) == 1
    assert needs_human_events[0].application_id == APP_ID
    assert "수정요청" in needs_human_events[0].message


# ─────────────────────────── 타이머 / 스케줄 ───────────────────────────
async def test_approval_timeout_expires(env: WorkflowEnvironment):
    """72시간 무응답 → expired. 무한 대기 워크플로우를 만들지 않는다."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(approval_timeout_hours=72))
        result = await handle.result()  # time-skipping 이 72시간을 즉시 통과시킨다

    assert result.state is ApplicationState.EXPIRED
    assert h.states(APP_ID)[-1] == "expired"
    assert h.notifier is not None
    assert any(e.kind == "EXPIRED" for e in h.notifier.notified)


async def test_reschedule_while_waiting_moves_the_timer(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)

        now = await env.get_current_time()
        original = now + timedelta(days=30)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal(scheduled_at=original))
        await _wait_state(handle, ApplicationState.SCHEDULED)

        # 재조정 전: 워크플로우는 원래 목표를 들고 대기 중이다
        assert (await handle.query(ApplicationWorkflow.state)).scheduled_at == original

        moved = now + timedelta(days=1)
        await handle.signal(ApplicationWorkflow.reschedule, RescheduleSignal(scheduled_at=moved))
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    # 대기 중에 타이머 목표가 실제로 교체됐다 (sleep 이었다면 signal 이 먹지 않는다)
    # projection 은 (run_id, state) 로 멱등 upsert 되므로 행 수가 아니라 값을 본다 (§4.1)
    row = h.row(APP_ID, "scheduled")
    assert row is not None and row.scheduled_at == moved


async def test_cancel_while_scheduled(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        now = await env.get_current_time()
        await handle.signal(
            ApplicationWorkflow.approve, ApproveSignal(scheduled_at=now + timedelta(days=30))
        )
        await _wait_state(handle, ApplicationState.SCHEDULED)
        await handle.signal(ApplicationWorkflow.cancel)
        result = await handle.result()

    assert result.state is ApplicationState.CANCELLED
    assert "executing" not in h.states(APP_ID)


# ─────────────────────── 실패 / 안전장치 ───────────────────────
async def test_ineligible_job_never_reaches_execution(env: WorkflowEnvironment):
    h = Harness(eligible=False, reject_reason="경력 요건 미달")
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        result = await handle.result()

    assert result.state is ApplicationState.REJECTED
    assert result.reason == "경력 요건 미달"
    assert h.states(APP_ID) == ["evaluating", "rejected"]


async def test_recipe_failure_goes_to_needs_human(env: WorkflowEnvironment):
    """DOM 변경 상황. M4 까지는 사람에게 넘긴다 — 절대 재시도로 밀어붙이지 않는다."""
    h = Harness(fail_selectors=frozenset({"#email"}))
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd())
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.NEEDS_HUMAN
    assert "RecipeExecutionError" in result.reason
    assert h.states(APP_ID)[-1] == "needs_human"
    attempt = h.attempt(APP_ID, 1)
    assert attempt is not None
    assert attempt.outcome is AttemptOutcome.FAILED
    assert attempt.error_code == "RecipeExecutionError"
    assert attempt.snapshot_key != ""


async def test_execution_failure_recovers_via_verify_before_needs_human(
    env: WorkflowEnvironment,
):
    """부분 제출 위험 방어 (§5): submit 이 오류로 보고돼도, 실제로는 제출됐을 수 있다.

    재개(여기서는 예외 처리 직후) 시 verify_submission 을 먼저 돌려서 확인되면 사람에게
    넘기지 않고 완료로 처리한다 — 안 그러면 이미 낸 지원서를 사람이 다시 손대게 된다.
    """
    h = Harness(fail_selectors=frozenset({"#submit"}), verified=True)
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=False))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert "제출 확인됨" in result.reason
    attempt = h.attempt(APP_ID, 1)
    assert attempt is not None
    assert attempt.outcome is AttemptOutcome.SUCCEEDED
    assert attempt.mode is ExecutionMode.LIVE


async def test_dry_run_never_submits(env: WorkflowEnvironment):
    """DRY_RUN_ONLY=true 면 submit 이 실행되지 않는다 (§9.5)."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=True))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert result.submitted_at is None, "dry_run 인데 제출 시각이 기록됐다"


async def test_live_mode_submits_and_verifies(env: WorkflowEnvironment):
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=False))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.COMPLETED
    assert result.submitted_at is not None
    assert "verifying" in h.states(APP_ID)
    attempt = h.attempt(APP_ID, 1)
    assert attempt is not None
    assert attempt.mode is ExecutionMode.LIVE
    assert attempt.outcome is AttemptOutcome.SUCCEEDED
    assert attempt.submitted_at is not None


async def test_verification_failure_goes_to_needs_human(env: WorkflowEnvironment):
    """제출은 됐는데 확인이 안 되면 성공으로 처리하지 않는다 (§5 부분 제출 위험)."""
    h = Harness(verified=False)
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(dry_run_only=False))
        await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
        await handle.signal(ApplicationWorkflow.approve, ApproveSignal())
        result = await handle.result()

    assert result.state is ApplicationState.NEEDS_HUMAN
    assert "제출 확인 실패" in result.reason
    attempt = h.attempt(APP_ID, 1)
    assert attempt is not None
    assert attempt.outcome is AttemptOutcome.UNKNOWN, "제출됐는지 결론이 안 났다 (§5)"
    assert attempt.error_code == "verify_failed"


async def test_unknown_platform_url_is_refused(env: WorkflowEnvironment):
    """allowlist 밖 도메인에는 자동화를 돌리지 않는다 (§3)."""
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _cmd(job_url="https://unknown.example.com/x"))
        with pytest.raises(WorkflowFailureError):
            await handle.result()


# ─────────────────────────── helper ───────────────────────────
async def _wait_state(
    handle: WorkflowHandle[ApplicationWorkflow, object], target: ApplicationState
) -> None:
    """워크플로우 query 로 상태를 기다린다 — DB 를 폴링하지 않는다 (§4.1)."""
    for _ in range(200):
        view = await handle.query(ApplicationWorkflow.state)
        if view.state is target:
            return
        await _tick()
    raise AssertionError(f"상태가 {target} 에 도달하지 않았다")


async def _tick() -> None:
    import asyncio

    await asyncio.sleep(0.05)
