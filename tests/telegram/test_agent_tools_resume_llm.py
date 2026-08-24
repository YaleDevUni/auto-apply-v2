"""resume_llm_generation 도구 (telegram/_agent_tools_resume_llm.py, §11.2c).

`ApplicationWorkflow.pending_llm_resume` query 로 멈춰 있는 child ResumeWorkflow 의 id 를
알아내 그 id 로 직접 `ResumeWorkflow.retry_now` signal 을 보내는 배선만 검증한다 — 실제
pause-and-resume 동작 자체는 tests/workflows/test_resume.py 가 `WorkflowEnvironment` 로 본다.
"""

from temporalio.service import RPCError, RPCStatusCode

from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.telegram._agent_tools_resume_llm import _resume_llm_generation
from tests.conftest import Harness


class _FakeHandle:
    def __init__(
        self,
        *,
        query_result: str = "",
        query_error: Exception | None = None,
        signal_error: Exception | None = None,
    ) -> None:
        self._query_result = query_result
        self._query_error = query_error
        self._signal_error = signal_error
        self.signalled = False

    async def query(self, _fn: object) -> str:
        if self._query_error:
            raise self._query_error
        return self._query_result

    async def signal(self, _fn: object) -> None:
        if self._signal_error:
            raise self._signal_error
        self.signalled = True


class _FakeClient:
    def __init__(self, handles: dict[str, _FakeHandle]) -> None:
        self._handles = handles

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        return self._handles[workflow_id]


def _container() -> Container:
    return Harness().container(settings=Settings(storage="memory", llm_provider="stub"))


async def test_resume_llm_generation_requires_application_id():
    message = await _resume_llm_generation({}, _container(), _FakeClient({}))

    assert "application_id" in message


async def test_resume_llm_generation_reports_when_application_not_found():
    client = _FakeClient(
        {"application-app_1": _FakeHandle(query_error=RPCError("nf", RPCStatusCode.NOT_FOUND, b""))}
    )

    message = await _resume_llm_generation({"application_id": "app_1"}, _container(), client)

    assert "찾을 수 없습니다" in message


async def test_resume_llm_generation_reports_when_nothing_paused():
    client = _FakeClient({"application-app_1": _FakeHandle(query_result="")})

    message = await _resume_llm_generation({"application_id": "app_1"}, _container(), client)

    assert "재개할 이력서 생성 대기가 없습니다" in message


async def test_resume_llm_generation_signals_the_paused_resume_workflow():
    resume_handle = _FakeHandle()
    client = _FakeClient(
        {
            "application-app_1": _FakeHandle(query_result="resume-app_1-1"),
            "resume-app_1-1": resume_handle,
        }
    )

    message = await _resume_llm_generation({"application_id": "app_1"}, _container(), client)

    assert resume_handle.signalled
    assert "재개하도록 신호를 보냈습니다" in message


async def test_resume_llm_generation_reports_when_resume_workflow_already_gone():
    client = _FakeClient(
        {
            "application-app_1": _FakeHandle(query_result="resume-app_1-1"),
            "resume-app_1-1": _FakeHandle(
                signal_error=RPCError("gone", RPCStatusCode.NOT_FOUND, b"")
            ),
        }
    )

    message = await _resume_llm_generation({"application_id": "app_1"}, _container(), client)

    assert "이미 끝났거나 대기 중이 아닙니다" in message
