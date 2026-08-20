"""ApplicationWorkflow 의 repair 트리거 헬퍼 (ARCHITECTURE.md §2.4). `_execution.py`가 감지한

RecipeExecutionError(`ExecutionOutcome.repair`)를 받아 `AutomationRepairWorkflow` child 를
부른다. `application.py`가 200줄을 넘기지 않도록 이 구간만 분리했다 — `_execution.py`/
`_revision.py`와 같은 이유.

같은 폼이 다른 지원 건에서도 동시에 실패하면 dedupe id(`repair-{platform}-{form_hash}`)가
겹쳐 `WorkflowAlreadyStartedError`가 난다. "이미 도는 수선에 붙어서 결과를 같이 기다리기"는
채택하지 않았다 — Temporal 워크플로우 코드 안에서 child 가 아닌 임의 워크플로우의 완료를
기다릴 표준 API 가 없다(탐색 단계에서 확인, 메모리 automation-repair-workflow-m4-phase1).
대신 이번 지원은 그냥 포기하고 사람에게 넘긴다 — 진행 중인 수선이 끝나 recipe 가 승격되면
다음 지원 시도가 `load_active_recipe`로 그 결과를 자연히 집어간다.
"""

from temporalio import workflow
from temporalio.exceptions import ChildWorkflowError, WorkflowAlreadyStartedError

from auto_apply.contracts.dto import ExecutionContext, RepairInput
from auto_apply.workflows._execution import RepairTrigger
from auto_apply.workflows.repair import AutomationRepairWorkflow

QUEUE_AI = "ai"


async def run_repair(
    platform: str, trigger: RepairTrigger, ctx: ExecutionContext
) -> tuple[bool, str]:
    """(promoted, reason) 을 돌려준다. 절대 예외를 올리지 않는다 — 호출자는 항상 이 실행

    시도를 NEEDS_HUMAN 으로 마무리하고, promoted 여부만 사유 문구에 반영한다.
    """
    try:
        result = await workflow.execute_child_workflow(
            AutomationRepairWorkflow.run,
            RepairInput(
                platform=platform,
                form_hash=trigger.form_hash,
                snapshot_key=trigger.snapshot_key,
                failed_version=trigger.failed_version,
                ctx=ctx,
            ),
            id=f"repair-{platform}-{trigger.form_hash}",
            task_queue=QUEUE_AI,
        )
    except WorkflowAlreadyStartedError:
        return False, "같은 폼에 대한 recipe 수선이 이미 다른 지원 건에서 진행 중이다"
    except ChildWorkflowError as e:
        return False, f"recipe 수선 워크플로우 실패: {e}"
    return result.promoted, result.reason
