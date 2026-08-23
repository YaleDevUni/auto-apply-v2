"""AutomationRepairWorkflow 테스트 (ARCHITECTURE.md §2.4).

`ApplicationWorkflow`가 이 워크플로우를 부르는 통합 경로는
`test_application.py::test_recipe_failure_goes_to_needs_human`(수선 실패 경로)이 덮는다 —
여기서는 `AutomationRepairWorkflow` 자체를 client 로 직접 시작해서 §2.4 다이어그램의 각
분기(정책 위반/스키마 위반/샌드박스 재시도/승격 승인·거절/dedupe)를 하나씩 검증한다.
"""

import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.contracts.dto import ApproveSignal, ExecutionContext, RejectSignal, RepairInput
from auto_apply.domain.enums import RecipePolicyBlockerCode
from auto_apply.domain.errors import PolicyViolation
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_BROWSER, QUEUE_DEFAULT
from auto_apply.workflows.repair import AutomationRepairWorkflow
from tests.conftest import JOB_URL, Harness

pytestmark = pytest.mark.temporal

PLATFORM = "fixture"
# tests.conftest.sample_recipe() 의 form_hash 와 맞춰야 repair dedupe id(§2.4)가 겹친다.
FORM_HASH = "h-fixture-1"


def _req(
    *, failed_version: int = 1, snapshot_key: str = "dom-snapshots/fixture/replay.html"
) -> RepairInput:
    return RepairInput(
        platform=PLATFORM,
        form_hash=FORM_HASH,
        snapshot_key=snapshot_key,
        failed_version=failed_version,
        ctx=ExecutionContext(
            application_id="app_1", attempt=1, profile={"email": "test@example.com"}
        ),
    )


_FIXED_DIFF = {
    "actions": [
        {"type": "goto", "value_literal": JOB_URL},
        {"type": "fill", "selector": "#email-v2", "value_ref": "profile.email"},
        {"type": "assert_visible", "selector": "#form"},
        {"type": "submit", "selector": "#submit"},
    ],
    "success_signals": ["지원이 완료되었습니다"],
}


async def _start(
    client: Client, req: RepairInput
) -> WorkflowHandle[AutomationRepairWorkflow, object]:
    return await client.start_workflow(
        AutomationRepairWorkflow.run,
        req,
        id=f"repair-{req.platform}-{req.form_hash}",
        task_queue=QUEUE_AI,
    )


class _Workers:
    """repair 가 걸치는 세 큐(ai/default/browser) 를 모두 띄운다."""

    def __init__(self, client: Client, harness: Harness) -> None:
        self._client = client
        self._acts = harness.activities()
        self._workers: list[Worker] = []

    async def __aenter__(self) -> "_Workers":
        specs = [
            (QUEUE_AI, [AutomationRepairWorkflow]),
            (QUEUE_DEFAULT, []),
            (QUEUE_BROWSER, []),
        ]
        for queue, wfs in specs:
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


# ─────────────── §2.4a 사람 확인 관문 (수선을 시작하기 전) ───────────────
async def test_denied_confirmation_skips_repair_and_keeps_recipe_active(env: WorkflowEnvironment):
    """ "recipe 문제가 아니다"를 고르면 LLM 도 안 돌고 격리도 안 한다 — recipe 는 active

    그대로라 다른 지원 건은 계속 제출할 수 있다(§2.4a 의 핵심 요구).
    """
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await handle.signal(
            AutomationRepairWorkflow.deny_broken,
            RejectSignal(nonce=await _wait_nonce(h, kind="confirm")),
        )
        result = await handle.result()

    assert result.promoted is False
    assert "사람이 recipe 문제가 아니라고 판단했다" in result.reason
    assert h.notifier is not None
    assert any(e.kind == "RECIPE_REPAIR_DECLINED" for e in h.notifier.notified)
    # 승격 승인까지 간 적이 없다 = LLM diff/샌드박스가 아예 안 돌았다
    assert not any(req.repair_promotion for req, _ in h.notifier.issued)
    assert (await h.recipes.active(PLATFORM)).status == "active"


async def test_no_answer_defaults_to_not_repairing(env: WorkflowEnvironment):
    """무응답의 기본값은 "아무것도 안 한다" — 사람이 확정하기 전엔 제출을 막지 않는다."""
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        result = await handle.result()  # time-skipping 이 24시간 무응답을 즉시 통과시킨다

    assert result.promoted is False
    assert (await h.recipes.active(PLATFORM)).status == "active"


async def test_confirmed_breakage_quarantines_recipe_before_repairing(env: WorkflowEnvironment):
    """확정하면 그 즉시 격리된다 — 수선이 끝날 때까지 그 플랫폼 지원은 실행조차 안 된다."""
    h = Harness(repair_diff_payloads=[])  # diff 없이 곧 포기 → 격리된 채로 끝난다
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        await handle.result()

    assert (await h.recipes.versions(PLATFORM))[0].status == "quarantined"
    with pytest.raises(PolicyViolation):
        await h.recipes.active(PLATFORM)
    assert h.notifier is not None
    assert any("격리했습니다" in e.message for e in h.notifier.notified)


async def test_successful_promotion_lifts_quarantine(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        await handle.signal(
            AutomationRepairWorkflow.approve, ApproveSignal(nonce=await _wait_nonce(h))
        )
        assert (await handle.result()).promoted is True

    active = await h.recipes.active(PLATFORM)
    assert (active.version, active.status) == (2, "active")


async def test_diagnosis_of_already_applied_page_reaches_the_human(env: WorkflowEnvironment):
    """실측 사고 재현(§2.4a): `지원완료` 페이지에서 난 실패라면 확인 메시지가 "recipe 문제가

    아닐 가능성이 높다"고 먼저 말해줘야 한다 — 사람이 그걸 보고 ❌ 를 고를 수 있어야 한다.
    """
    h = Harness()
    snapshot_key = "dom-snapshots/fixture/h-fixture-1/app_1/attempt-1-2.html"
    async with _Workers(env.client, h):
        await h.store.put(
            snapshot_key,
            (
                "<html><body><p>" + "본 채용정보는 무단전재 금지. " * 40 + "</p>"
                "<button>지원완료</button></body></html>"
            ).encode(),
            content_type="text/html",
        )
        handle = await _start(env.client, _req(snapshot_key=snapshot_key))
        await _wait_nonce(h, kind="confirm")
        await handle.signal(AutomationRepairWorkflow.deny_broken, RejectSignal())
        await handle.result()

    assert h.notifier is not None
    confirm = next(req for req, _ in h.notifier.issued if req.repair_confirm)
    assert "recipe 문제가 아닐 가능성이 높다" in confirm.summary
    assert "이미 지원한 공고" in confirm.summary


# ────────────────────── §2.4 node B~J (관문 통과 이후) ──────────────────────
async def test_schema_violation_exhausted_gives_up(env: WorkflowEnvironment):
    """repair_diff_payloads 를 안 채우면 LLM 이 빈 payload 를 낸다 — RecipeDiffSchema 필수

    필드 누락으로 재프롬프트를 다 써도 실패해 사람에게 넘긴다(승격 승인 요청까지 가지 않는다).
    """
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        result = await handle.result()

    assert result.promoted is False
    assert "LLM diff 제안 실패" in result.reason
    assert h.notifier is not None
    assert any(e.kind == "RECIPE_REPAIR_STARTED" for e in h.notifier.notified)
    assert any(e.kind == "RECIPE_REPAIR_FAILED" for e in h.notifier.notified)


async def test_policy_violation_gives_up_without_sandbox(env: WorkflowEnvironment):
    """CREDENTIAL_FIELD 위반이면 샌드박스/승격 승인 없이 곧바로 포기한다."""
    h = Harness(
        repair_diff_payloads=[
            {
                "actions": [
                    {"type": "goto", "value_literal": JOB_URL},
                    {"type": "fill", "selector": "#password", "value_literal": "hunter2"},
                ],
                "success_signals": ["완료"],
            }
        ]
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        result = await handle.result()

    assert result.promoted is False
    assert RecipePolicyBlockerCode.CREDENTIAL_FIELD in result.reason


async def test_sandbox_failure_retries_once_then_gives_up(env: WorkflowEnvironment):
    """샌드박스에서 계속 실패하는 selector 를 내면 한 번 더 시도하고(§2.4 "재시도 < 2") 그래도

    안 되면 포기한다 — repair_diff_payloads 를 2개 채워 두 번 다 같은 실패 selector 를 쓰게 한다.
    """
    bad_diff = {
        "actions": [
            {"type": "goto", "value_literal": JOB_URL},
            {"type": "fill", "selector": "#still-broken", "value_ref": "profile.email"},
            {"type": "submit", "selector": "#submit"},
        ],
        "success_signals": ["완료"],
    }
    h = Harness(
        repair_diff_payloads=[bad_diff, bad_diff],
        fail_selectors=frozenset({"#still-broken"}),
    )
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        result = await handle.result()

    assert result.promoted is False
    assert "샌드박스 dry-run 실패" in result.reason


async def test_sandbox_pass_then_approve_promotes(env: WorkflowEnvironment):
    """§2.4 happy path 전 구간: diff 제안 → 정책 통과 → 샌드박스 통과 → candidate 저장 → 승격."""
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        nonce = await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.approve, ApproveSignal(nonce=nonce))
        result = await handle.result()

    assert result.promoted is True
    assert result.new_version == 2


async def test_promotion_rejected_keeps_candidate_not_promoted(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        nonce = await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.reject, RejectSignal(nonce=nonce))
        result = await handle.result()

    assert result.promoted is False
    assert result.new_version == 2  # candidate 는 저장됐지만 active 로 승격되진 않았다


async def test_promotion_timeout_gives_up(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        result = await handle.result()  # time-skipping 이 72시간 무응답을 즉시 통과시킨다

    assert result.promoted is False


async def test_wrong_nonce_is_ignored(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _confirm_broken(handle, h)
        real_nonce = await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.approve, ApproveSignal(nonce="stale-nonce"))
        # 위조 nonce 는 무시된다 — 진짜 nonce 로 다시 승인해야 끝난다
        await handle.signal(AutomationRepairWorkflow.approve, ApproveSignal(nonce=real_nonce))
        result = await handle.result()

    assert result.promoted is True


async def test_concurrent_repair_for_same_form_is_deduped(env: WorkflowEnvironment):
    """동시에 같은 폼이 실패한 두 지원이 같은 dedupe id 로 시작을 시도하면 두 번째는

    WorkflowAlreadyStartedError 를 받는다 — `workflows/_repair.py`가 이걸 잡아 그 지원만
    포기시키고 사람에게 넘긴다(§2.4, 메모리 automation-repair-workflow-m4-phase1).
    """
    h = Harness()
    async with _Workers(env.client, h):
        first = await _start(env.client, _req())
        with pytest.raises(WorkflowAlreadyStartedError):
            await _start(env.client, _req())
        # 첫 실행은 정리한다 — §2.4a 관문에서 "깨진 게 아니다"로 끝낸다.
        await first.signal(
            AutomationRepairWorkflow.deny_broken,
            RejectSignal(nonce=await _wait_nonce(h, kind="confirm")),
        )
        await first.result()


async def _wait_nonce(h: Harness, *, kind: str = "promotion") -> str:
    """`kind` 로 어느 승인 요청의 nonce 인지 고른다 — 한 워크플로우가 같은 application_id 로

    두 번(파손 확정 §2.4a → 승격 §2.4) 물어보기 때문에 last_ticket 만으로는 못 가른다.
    """
    assert h.notifier is not None
    for _ in range(300):
        for req, nonce in h.notifier.issued:
            if kind == "confirm" and req.repair_confirm:
                return nonce
            if kind == "promotion" and req.repair_promotion:
                return nonce
        await _tick()
    raise AssertionError(f"{kind} nonce 가 발급되지 않았다")


async def _confirm_broken(
    handle: WorkflowHandle[AutomationRepairWorkflow, object], h: Harness
) -> None:
    """§2.4a 관문 통과 — "진짜 깨졌다"를 확정해야 수선이 시작된다. 이 아래 테스트들은

    전부 그 다음 구간(§2.4 node B~J)을 보는 것이라 이 헬퍼로 관문만 넘긴다.
    """
    await handle.signal(
        AutomationRepairWorkflow.confirm_broken,
        ApproveSignal(nonce=await _wait_nonce(h, kind="confirm")),
    )


async def _tick() -> None:
    import asyncio

    await asyncio.sleep(0.05)
