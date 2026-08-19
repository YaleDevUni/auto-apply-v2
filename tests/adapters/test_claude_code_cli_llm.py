"""ClaudeCodeCliLLM — API 키 대신 `claude` CLI subprocess 를 쓰는 LLMClient 구현 (§11.2).

subprocess 는 뜨지 않는다 — `asyncio.create_subprocess_exec` 를 모킹해서 어댑터가 만드는
인자·에러 매핑·세션 재사용 로직만 검증한다(실제 CLI 대상 실측은 코드 리뷰 시 수기로 확인함).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, ConfigDict

from auto_apply.adapters.llm import claude_code_cli as module
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.domain.errors import (
    LLMAuthRequired,
    LLMExecutionError,
    LLMQuotaExceeded,
    LLMSchemaViolation,
)


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def _envelope(**overrides: object) -> bytes:
    base = {
        "is_error": False,
        "result": "hello",
        "structured_output": {"text": "hi"},
        "total_cost_usd": 0.001,
        "usage": {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }
    base.update(overrides)
    return json.dumps(base).encode()


def _mock_proc(stdout: bytes, *, returncode: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, b"")
    proc.returncode = returncode
    return proc


def _patch_exec(proc: AsyncMock):
    return patch.object(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))


async def test_complete_returns_result_field():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope(result="이력서 요약"))):
        assert await llm.complete("prompt") == "이력서 요약"


async def test_structured_validates_structured_output_field():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope(structured_output={"text": "ok"}))):
        out = await llm.structured("prompt", _Schema)
    assert out == _Schema(text="ok")


async def test_structured_raises_schema_violation_when_field_missing():
    llm = ClaudeCodeCliLLM()
    envelope = json.loads(_envelope())
    del envelope["structured_output"]
    with (
        _patch_exec(_mock_proc(json.dumps(envelope).encode())),
        pytest.raises(LLMSchemaViolation),
    ):
        await llm.structured("prompt", _Schema)


async def test_structured_raises_schema_violation_on_pydantic_mismatch():
    llm = ClaudeCodeCliLLM()
    with (
        _patch_exec(_mock_proc(_envelope(structured_output={"wrong_field": 1}))),
        pytest.raises(LLMSchemaViolation),
    ):
        await llm.structured("prompt", _Schema)


async def test_nonzero_exit_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    proc = _mock_proc(b"", returncode=1)
    proc.communicate.return_value = (b"", b"auth error")
    with _patch_exec(proc), pytest.raises(LLMExecutionError, match="auth error"):
        await llm.complete("prompt")


async def test_invalid_json_stdout_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(b"not json")), pytest.raises(LLMExecutionError):
        await llm.complete("prompt")


async def test_is_error_envelope_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope(is_error=True))), pytest.raises(LLMExecutionError):
        await llm.complete("prompt")


async def test_logged_out_envelope_raises_llm_auth_required():
    """실측 시그니처(CLAUDE_CONFIG_DIR 를 빈 디렉터리로 돌려 로그아웃 상태 재현, 2026-08-19)."""
    llm = ClaudeCodeCliLLM()
    envelope = _envelope(
        is_error=True,
        result="Not logged in · Please run /login",
        api_error_status=None,
        terminal_reason="api_error",
    )
    proc = _mock_proc(envelope, returncode=1)
    with _patch_exec(proc), pytest.raises(LLMAuthRequired, match="로그인"):
        await llm.complete("prompt")


async def test_budget_exhausted_envelope_raises_llm_quota_exceeded():
    """실측 시그니처(--max-budget-usd 를 극단적으로 낮춰 한도초과 재현, 2026-08-19)."""
    llm = ClaudeCodeCliLLM()
    envelope = _envelope(
        is_error=True,
        result=None,
        terminal_reason="budget_exhausted",
        subtype="error_max_budget_usd",
        errors=["Reached maximum budget ($0.000001)"],
    )
    proc = _mock_proc(envelope, returncode=1)
    with _patch_exec(proc), pytest.raises(LLMQuotaExceeded, match="한도"):
        await llm.complete("prompt")


async def test_rate_limited_api_error_status_raises_llm_quota_exceeded():
    """result 문자열이 안 잡혀도 api_error_status=429 만으로 분류할 수 있어야 한다."""
    llm = ClaudeCodeCliLLM()
    envelope = _envelope(is_error=True, result="upstream rate limit exceeded", api_error_status=429)
    proc = _mock_proc(envelope, returncode=1)
    with _patch_exec(proc), pytest.raises(LLMQuotaExceeded):
        await llm.complete("prompt")


async def test_harness_is_stripped_down_in_every_call():
    """툴 0개·MCP 0개·스킬 비활성·설정 소스 무시·모델 명시 — 하나라도 빠지면 과금/토큰 낭비."""
    llm = ClaudeCodeCliLLM(model="claude-sonnet-5")
    captured: list[str] = []

    async def fake_exec(*args: str, **_kwargs: object) -> AsyncMock:
        captured.extend(args)
        return _mock_proc(_envelope())

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("prompt")

    assert "--tools" in captured and captured[captured.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in captured
    assert "--disable-slash-commands" in captured
    idx = captured.index("--setting-sources")
    assert captured[idx + 1] == ""
    assert "--model" in captured and captured[captured.index("--model") + 1] == "claude-sonnet-5"
    assert "--no-session-persistence" in captured  # cache_key 없는 1회성 호출


async def test_no_cache_key_never_persists_a_session():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope())):
        await llm.complete("a")
        await llm.complete("b")
    assert llm._sessions == {}


async def test_same_cache_key_resumes_the_session_on_second_call():
    llm = ClaudeCodeCliLLM()
    captured_calls: list[list[str]] = []

    async def fake_exec(*args: str, **_kwargs: object) -> AsyncMock:
        captured_calls.append(list(args))
        return _mock_proc(_envelope())

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("first", cache_key="user-1")
        await llm.complete("second", cache_key="user-1")

    first_args, second_args = captured_calls
    assert "--session-id" in first_args
    assert "--resume" not in first_args
    session_id = first_args[first_args.index("--session-id") + 1]

    assert "--resume" in second_args
    assert second_args[second_args.index("--resume") + 1] == session_id
    assert "--session-id" not in second_args
    assert "--no-session-persistence" not in first_args
    assert "--no-session-persistence" not in second_args


async def test_different_cache_keys_get_independent_sessions():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope())):
        await llm.complete("a", cache_key="user-1")
        await llm.complete("b", cache_key="user-2")
    assert llm._sessions["user-1"].id != llm._sessions["user-2"].id


async def test_session_rotates_after_max_turns(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "_MAX_TURNS_PER_SESSION", 1)
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope())):
        await llm.complete("a", cache_key="user-1")
        first_id = llm._sessions["user-1"].id
        await llm.complete("b", cache_key="user-1")
        second_id = llm._sessions["user-1"].id
    assert first_id != second_id


async def test_session_rotates_after_ttl_expires(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "_SESSION_TTL_SECONDS", 0)
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_envelope())):
        await llm.complete("a", cache_key="user-1")
        first_id = llm._sessions["user-1"].id
        await llm.complete("b", cache_key="user-1")
        second_id = llm._sessions["user-1"].id
    assert first_id != second_id
