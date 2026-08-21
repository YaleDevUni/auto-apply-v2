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
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_BROWSER, QUEUE_DEFAULT
from auto_apply.workflows.repair import AutomationRepairWorkflow
from tests.conftest import JOB_URL, Harness

pytestmark = pytest.mark.integration

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


async def test_schema_violation_exhausted_gives_up(env: WorkflowEnvironment):
    """repair_diff_payloads 를 안 채우면 LLM 이 빈 payload 를 낸다 — RecipeDiffSchema 필수

    필드 누락으로 재프롬프트를 다 써도 실패해 사람에게 넘긴다(승격 승인 요청까지 가지 않는다).
    """
    h = Harness()
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
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
        result = await handle.result()

    assert result.promoted is False
    assert "샌드박스 dry-run 실패" in result.reason


async def test_sandbox_pass_then_approve_promotes(env: WorkflowEnvironment):
    """§2.4 happy path 전 구간: diff 제안 → 정책 통과 → 샌드박스 통과 → candidate 저장 → 승격."""
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        nonce = await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.approve, ApproveSignal(nonce=nonce))
        result = await handle.result()

    assert result.promoted is True
    assert result.new_version == 2


async def test_promotion_rejected_keeps_candidate_not_promoted(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        nonce = await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.reject, RejectSignal(nonce=nonce))
        result = await handle.result()

    assert result.promoted is False
    assert result.new_version == 2  # candidate 는 저장됐지만 active 로 승격되진 않았다


async def test_promotion_timeout_gives_up(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        result = await handle.result()  # time-skipping 이 72시간 무응답을 즉시 통과시킨다

    assert result.promoted is False


async def test_wrong_nonce_is_ignored(env: WorkflowEnvironment):
    h = Harness(repair_diff_payloads=[_FIXED_DIFF])
    async with _Workers(env.client, h):
        handle = await _start(env.client, _req())
        await _wait_nonce(h)
        await handle.signal(AutomationRepairWorkflow.approve, ApproveSignal(nonce="stale-nonce"))
        # 위조 nonce 는 무시된다 — 진짜 nonce 로 다시 승인해야 끝난다
        real_nonce = h.notifier.last_ticket[f"{PLATFORM}-{FORM_HASH}"]  # type: ignore[union-attr]
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
        # 첫 실행은 정리한다 — 빈 diff payload 라 곧 포기하고 끝난다.
        await first.result()


async def _wait_nonce(h: Harness) -> str:
    key = f"{PLATFORM}-{FORM_HASH}"
    assert h.notifier is not None
    for _ in range(300):
        nonce = h.notifier.last_ticket.get(key)
        if nonce is not None:
            return nonce
        await _tick()
    raise AssertionError("승격 승인 nonce 가 발급되지 않았다")


async def _tick() -> None:
    import asyncio

    await asyncio.sleep(0.05)
