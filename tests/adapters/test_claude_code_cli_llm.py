"""ClaudeCodeCliLLM — API 키 대신 `claude` CLI subprocess 를 쓰는 LLMClient 구현 (§11.2).

subprocess 는 뜨지 않는다 — `asyncio.create_subprocess_exec` 를 모킹해서 어댑터가 만드는
인자·stdin 페이로드·에러 매핑만 검증한다(실제 CLI 대상 실측은 [[claude-cli-prompt-cache-redesign]]
에 기록됨).
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


def _result_line(**overrides: object) -> bytes:
    base = {
        "type": "result",
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
    with _patch_exec(_mock_proc(_result_line(result="이력서 요약"))):
        assert await llm.complete("prompt") == "이력서 요약"


async def test_structured_validates_structured_output_field():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_result_line(structured_output={"text": "ok"}))):
        out = await llm.structured("prompt", _Schema)
    assert out == _Schema(text="ok")


async def test_structured_raises_schema_violation_when_field_missing():
    llm = ClaudeCodeCliLLM()
    envelope = json.loads(_result_line())
    del envelope["structured_output"]
    with (
        _patch_exec(_mock_proc(json.dumps(envelope).encode())),
        pytest.raises(LLMSchemaViolation),
    ):
        await llm.structured("prompt", _Schema)


async def test_structured_raises_schema_violation_on_pydantic_mismatch():
    llm = ClaudeCodeCliLLM()
    with (
        _patch_exec(_mock_proc(_result_line(structured_output={"wrong_field": 1}))),
        pytest.raises(LLMSchemaViolation),
    ):
        await llm.structured("prompt", _Schema)


async def test_nonzero_exit_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    proc = _mock_proc(b"", returncode=1)
    proc.communicate.return_value = (b"", b"auth error")
    with _patch_exec(proc), pytest.raises(LLMExecutionError, match="auth error"):
        await llm.complete("prompt")


async def test_no_result_line_in_stdout_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(b"not json")), pytest.raises(LLMExecutionError):
        await llm.complete("prompt")


async def test_is_error_envelope_raises_execution_error():
    llm = ClaudeCodeCliLLM()
    with _patch_exec(_mock_proc(_result_line(is_error=True))), pytest.raises(LLMExecutionError):
        await llm.complete("prompt")


async def test_logged_out_envelope_raises_llm_auth_required():
    """실측 시그니처(CLAUDE_CONFIG_DIR 를 빈 디렉터리로 돌려 로그아웃 상태 재현, 2026-08-19)."""
    llm = ClaudeCodeCliLLM()
    envelope = _result_line(
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
    envelope = _result_line(
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
    envelope = _result_line(
        is_error=True, result="upstream rate limit exceeded", api_error_status=429
    )
    proc = _mock_proc(envelope, returncode=1)
    with _patch_exec(proc), pytest.raises(LLMQuotaExceeded):
        await llm.complete("prompt")


async def test_harness_is_stripped_down_in_every_call():
    """툴 0개·MCP 0개·스킬 비활성·설정 소스 무시·모델 명시 — 하나라도 빠지면 과금/토큰 낭비."""
    llm = ClaudeCodeCliLLM(model="claude-sonnet-5")
    captured: list[str] = []

    async def fake_exec(*args: str, **_kwargs: object) -> AsyncMock:
        captured.extend(args)
        return _mock_proc(_result_line())

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("prompt")

    assert "--tools" in captured and captured[captured.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in captured
    assert "--disable-slash-commands" in captured
    idx = captured.index("--setting-sources")
    assert captured[idx + 1] == ""
    assert "--model" in captured and captured[captured.index("--model") + 1] == "claude-sonnet-5"
    assert "--no-session-persistence" in captured  # 세션을 전혀 안 남긴다(§ 재설계)
    assert "--input-format" in captured
    assert captured[captured.index("--input-format") + 1] == "stream-json"
    assert "--output-format" in captured
    assert captured[captured.index("--output-format") + 1] == "stream-json"


async def test_prompt_without_cache_prefix_sends_a_single_content_block():
    llm = ClaudeCodeCliLLM()
    captured_stdin: list[bytes] = []

    result = (_result_line(), b"")

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        proc = _mock_proc(result[0])

        async def fake_communicate(input: bytes) -> tuple[bytes, bytes]:
            captured_stdin.append(input)
            return result

        proc.communicate = fake_communicate
        return proc

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("그냥 프롬프트")

    payload = json.loads(captured_stdin[0])
    blocks = payload["message"]["content"]
    assert len(blocks) == 1
    assert blocks[0]["text"] == "그냥 프롬프트"
    assert "cache_control" not in blocks[0]


async def test_cache_prefix_becomes_a_separate_breakpointed_block():
    llm = ClaudeCodeCliLLM()
    captured_stdin: list[bytes] = []

    result = (_result_line(), b"")

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        proc = _mock_proc(result[0])

        async def fake_communicate(input: bytes) -> tuple[bytes, bytes]:
            captured_stdin.append(input)
            return result

        proc.communicate = fake_communicate
        return proc

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("변하는 접미어", cache_prefix="안정적인 접두어")

    payload = json.loads(captured_stdin[0])
    blocks = payload["message"]["content"]
    assert len(blocks) == 2
    assert blocks[0]["text"] == "안정적인 접두어"
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert blocks[1]["text"] == "변하는 접미어"
    assert "cache_control" not in blocks[1]


async def test_empty_prompt_with_cache_prefix_omits_the_second_block():
    """재프롬프트 루프 1번째 시도 — addition 이 빈 문자열이면 빈 텍스트 블록을 안 보낸다."""
    llm = ClaudeCodeCliLLM()
    captured_stdin: list[bytes] = []

    result = (_result_line(), b"")

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        proc = _mock_proc(result[0])

        async def fake_communicate(input: bytes) -> tuple[bytes, bytes]:
            captured_stdin.append(input)
            return result

        proc.communicate = fake_communicate
        return proc

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        await llm.complete("", cache_prefix="원본 프롬프트 전체")

    payload = json.loads(captured_stdin[0])
    blocks = payload["message"]["content"]
    assert len(blocks) == 1
    assert blocks[0]["text"] == "원본 프롬프트 전체"
