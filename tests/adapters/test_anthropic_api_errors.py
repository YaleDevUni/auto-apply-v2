"""Anthropic API 실패 → §A9 분류 (T3.8). 런타임과 텍스트 LLM 이 같은 번역(`api_errors`)을 쓴다.

API 키 없음·401·403 → LLMAuthRequired(FATAL), 잔액·지출 한도 400 → LLMQuotaExceeded(FATAL),
429·5xx·연결 실패 → LLMExecutionError(TRANSIENT). 에러 문구에 응답 본문·키가 없다.
"""

import httpx
import pytest
from anthropic import AsyncAnthropic

from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.contracts.agent import AgentLimits, ToolReply
from auto_apply.domain.errors import LLMAuthRequired, LLMExecutionError, LLMQuotaExceeded
from auto_apply.domain.failure import FailureKind, classify_failure
from tests.adapters.anthropic_kit import KEY, FakeMessagesApi, api_runtime

SECRET_BODY = "요청 일부 되풀이: 홍길동 010-1234-5678"


async def _never(name: str, args: object) -> ToolReply:
    raise AssertionError("도구에 닿으면 안 된다")


CASES = [
    (401, "authentication_error", SECRET_BODY, LLMAuthRequired, FailureKind.FATAL),
    (403, "permission_error", SECRET_BODY, LLMAuthRequired, FailureKind.FATAL),
    (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.",
     LLMQuotaExceeded, FailureKind.FATAL),
    (400, "invalid_request_error", "You have reached your specified API usage limits.",
     LLMQuotaExceeded, FailureKind.FATAL),
    (429, "rate_limit_error", SECRET_BODY, LLMExecutionError, FailureKind.TRANSIENT),
    (500, "api_error", SECRET_BODY, LLMExecutionError, FailureKind.TRANSIENT),
    (529, "overloaded_error", SECRET_BODY, LLMExecutionError, FailureKind.TRANSIENT),
]  # fmt: skip


@pytest.mark.parametrize(("status", "kind", "message", "exc", "failure"), CASES)
async def test_runtime_classifies_api_failures(tmp_path, status, kind, message, exc, failure):
    api = FakeMessagesApi().fail(status, kind, message)
    with pytest.raises(exc) as info:
        await api_runtime(api, tmp_path).run("s", (), _never, limits=AgentLimits())
    assert type(info.value) is exc  # 429 가 한도초과(FATAL)로 새지 않게
    assert classify_failure(info.value) is failure
    text = str(info.value)
    assert str(status) in text and kind in text
    assert "홍길동" not in text and KEY not in text
    assert api.transport.closed


async def test_missing_api_key_fails_before_any_request(tmp_path):
    api = FakeMessagesApi(["안 온다"])
    with pytest.raises(LLMAuthRequired, match="ANTHROPIC_API_KEY") as info:
        await api_runtime(api, tmp_path, key="").run("s", (), _never, limits=AgentLimits())
    assert classify_failure(info.value) is FailureKind.FATAL
    assert api.requests == []


async def test_connection_failure_is_transient(tmp_path):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("연결 거부", request=request)

    rt = api_runtime(FakeMessagesApi(), tmp_path)
    rt._transport = httpx.MockTransport(refuse)
    with pytest.raises(LLMExecutionError, match="연결") as info:
        await rt.run("s", (), _never, limits=AgentLimits())
    assert classify_failure(info.value) is FailureKind.TRANSIENT


@pytest.mark.parametrize(
    ("status", "kind", "exc"), [c[:2] + c[3:4] for c in CASES[:1] + CASES[4:5]]
)
async def test_text_llm_uses_the_same_translation(status, kind, exc):
    api = FakeMessagesApi().fail(status, kind, SECRET_BODY)
    llm = AnthropicLLM(KEY, model="claude-sonnet-5-5")
    llm._client = AsyncAnthropic(
        api_key=KEY, max_retries=0, http_client=httpx.AsyncClient(transport=api.transport)
    )
    with pytest.raises(exc) as info:
        await llm.complete("안녕")
    assert type(info.value) is exc and "홍길동" not in str(info.value)
    await llm._client.close()
