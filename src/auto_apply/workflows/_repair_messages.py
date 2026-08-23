"""`AutomationRepairWorkflow` 가 사람에게 보내는 메시지를 만드는 순수 함수들 (§2.4, §2.4a).

워크플로우 본체(`repair.py`)는 흐름(확인 → 격리 → diff → 샌드박스 → 승격)만 남기고, "무슨
문구로 물어볼 것인가"는 여기로 뺐다 — `_execution.py`/`_revision.py`를 `application.py`에서
뺀 것과 같은 이유(한 파일 = 한 책임). I/O 가 없으므로 activity 가 아니다(§11.3).

`DecisionRequest.application_id` 자리에 `f"{platform}-{form_hash}"`를 담는 규칙은
`telegram/bridge.py`가 `wf_id = f"repair-{...}"`를 복원하는 데 쓴다 — 확인(qa/qr)과
승격(pa/pr) 둘 다 같은 규칙이다.
"""

from auto_apply.contracts.dto import DecisionRequest, NotifyEvent, RepairDiagnosis, RepairInput
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.recipe_diagnosis import PageVerdict


def repair_id(req: RepairInput) -> str:
    return f"{req.platform}-{req.form_hash}"


def confirm_request(
    req: RepairInput, workflow_id: str, diagnosis: RepairDiagnosis
) -> DecisionRequest:
    """ "이 recipe 진짜 깨진 거 맞나요?" — 수선을 시작하기 전의 유일한 관문 (§2.4a).

    승인하면 recipe 가 격리돼 그 플랫폼 제출이 멈추므로, 그 결과를 문구에 그대로 적는다 —
    "승인 = 수선 시작"으로만 읽히면 사람이 제출 중단이라는 부작용을 모른 채 누른다.
    """
    likely_not_recipe = diagnosis.verdict is not PageVerdict.RECIPE_SUSPECTED
    lead = "🔎 recipe 문제가 아닐 가능성이 높다" if likely_not_recipe else "🔧 recipe 문제로 보인다"
    return DecisionRequest(
        application_id=repair_id(req),
        workflow_id=workflow_id,
        title=f"{req.platform} recipe v{req.failed_version} 실행 실패 — 진짜 깨진 건가요?",
        summary=(
            f"{lead}\n{diagnosis.summary}\n\n"
            f"[실패 사유]\n{req.failure_reason or '(사유 미상)'}\n\n"
            "✅ 를 누르면: recipe 를 격리해 이 플랫폼 제출을 멈추고 수선 에이전트를 돌립니다.\n"
            "❌ 를 누르면: 아무것도 안 바꿉니다 — recipe 는 그대로라 다른 지원 건은 "
            "계속 제출됩니다."
        ),
        repair_confirm=True,
    )


def promotion_request(
    req: RepairInput, workflow_id: str, candidate: AutomationRecipe
) -> DecisionRequest:
    return DecisionRequest(
        application_id=repair_id(req),
        workflow_id=workflow_id,
        title=f"{req.platform} recipe v{candidate.version} 승격 승인",
        summary=(
            f"actions {len(candidate.actions)}개, success_signals={candidate.success_signals}\n"
            "승격하면 격리도 함께 풀려 제출이 다시 시작됩니다."
        ),
        repair_promotion=True,
    )


def stand_down_event(req: RepairInput, diagnosis: RepairDiagnosis) -> NotifyEvent:
    return NotifyEvent(
        kind="RECIPE_REPAIR_DECLINED",
        application_id=repair_id(req),
        message=(
            f"{req.platform} recipe v{req.failed_version} 수선을 시작하지 않습니다 — "
            "recipe 는 active 그대로라 다른 지원 건은 계속 제출됩니다.\n"
            f"판정: {diagnosis.summary}"
        ),
    )


def started_event(req: RepairInput, quarantined: AutomationRecipe | None) -> NotifyEvent:
    held = (
        f"recipe v{quarantined.version} 를 격리했습니다 — 수선이 끝날 때까지 이 플랫폼 제출은 "
        "멈춥니다."
        if quarantined is not None
        else "⚠️ 격리에 실패했습니다 — 제출이 안 멈춘 상태로 수선만 진행합니다."
    )
    return NotifyEvent(
        kind="RECIPE_REPAIR_STARTED",
        application_id=repair_id(req),
        message=f"{req.platform} recipe v{req.failed_version} 수선 시작. {held}",
    )


def failed_event(req: RepairInput, reason: str, *, quarantined: bool) -> NotifyEvent:
    tail = (
        "\nrecipe 는 격리된 채로 남습니다 — 제출을 다시 열려면 텔레그램에서 "
        f"'{req.platform} recipe 격리 해제'라고 요청하세요(SUPERVISED candidate 로 복귀합니다)."
        if quarantined
        else ""
    )
    return NotifyEvent(
        kind="RECIPE_REPAIR_FAILED",
        application_id=repair_id(req),
        message=f"{req.platform}({req.form_hash}) recipe 수선 실패: {reason}{tail}",
    )
