"""claude CLI 한도초과로 일시정지된 이력서 생성을 재개하는 도구 (§11.2c).

`retry_application`(`_agent_tools_retry.py`)과 다르다 — 그건 이미 NEEDS_HUMAN 으로 완전히
끝난 지원 건을 처음부터 다시 시작한다(공고 수집부터 재승인까지 전부 반복). 이 도구는 워크플로우가
아직 살아서 멈춰 있는 상태(§11.2c pause-and-resume)를 그대로 이어가므로, 이미 끝난 단계
(공고 수집·평가)를 다시 돌지 않고 재승인도 새로 안 받는다.

`ApplicationWorkflow.pending_llm_resume` query 로 멈춰 있는 child ResumeWorkflow 의
workflow_id 를 알아내 그 id 로 직접 `ResumeWorkflow.retry_now` signal 을 보낸다 — 부모를
거치지 않는다(부모는 그냥 child 를 await 하며 같이 멈춰 있을 뿐이라 relay 할 상태가 없다).
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.resume import ResumeWorkflow

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]


async def _resume_llm_generation(args: dict[str, str], _c: Container, client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    try:
        handle = client.get_workflow_handle(f"application-{application_id}")
        resume_wf_id = await handle.query(ApplicationWorkflow.pending_llm_resume)
    except RPCError as e:
        return f"{application_id}: 워크플로우를 찾을 수 없습니다 ({e.message})"
    if not resume_wf_id:
        return f"{application_id}: 재개할 이력서 생성 대기가 없습니다."
    try:
        await client.get_workflow_handle(resume_wf_id).signal(ResumeWorkflow.retry_now)
    except RPCError as e:
        return f"{application_id}: 이력서 생성이 이미 끝났거나 대기 중이 아닙니다 ({e.message})"
    return f"{application_id}: 이력서 생성을 재개하도록 신호를 보냈습니다."


RESUME_LLM_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "resume_llm_generation": (
        "claude CLI 사용량 한도초과로 멈춰 있는 이력서 생성을 이어서 진행한다"
        " (application_id 필요). 한도가 풀렸거나 리셋된 뒤 '한도 풀렸으니 재개해줘',"
        " '이어서 해줘' 같은 요청에 쓴다 — retry_application 과 달리 워크플로우를 처음부터"
        " 다시 시작하지 않고 멈춰 있던 지점(이력서 생성 중)부터 그대로 이어간다. 멈춰 있는"
        " 이력서 생성이 없으면(예: 이미 완료됐거나 다른 이유로 NEEDS_HUMAN 이 됨) 아무 효과가"
        " 없다고 알려준다.",
        ("application_id",),
        _resume_llm_generation,
    ),
}
