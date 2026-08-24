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


async def test_cache_control_overflow_retries_once_without_cache_control():
    """실측(2026-08-21): 큰 프롬프트에서 CLI 가 "maximum of 4 blocks with cache_control"
    400 을 낼 때가 있다 — cache_control 없이 한 번 더 시도해서 정확성을 지켜야 한다."""
    llm = ClaudeCodeCliLLM()
    captured_stdin: list[bytes] = []
    overflow = _result_line(
        is_error=True,
        result="API Error: 400 A maximum of 4 blocks with cache_control may be provided. Found 5.",
    )
    ok = _result_line(result="성공")
    responses = [overflow, ok]

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        stdout = responses.pop(0)
        proc = _mock_proc(stdout)

        async def fake_communicate(input: bytes) -> tuple[bytes, bytes]:
            captured_stdin.append(input)
            return stdout, b""

        proc.communicate = fake_communicate
        return proc

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        result = await llm.complete("변하는 접미어", cache_prefix="안정적인 접두어")

    assert result == "성공"
    assert len(captured_stdin) == 2
    first_blocks = json.loads(captured_stdin[0])["message"]["content"]
    assert first_blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    second_blocks = json.loads(captured_stdin[1])["message"]["content"]
    assert "cache_control" not in second_blocks[0]
    assert second_blocks[0]["text"] == "안정적인 접두어"


async def test_cache_control_overflow_persisting_still_raises_execution_error():
    """폴백 시도까지 실패하면(다른 원인) 정상적으로 분류된 에러를 낸다 — 무한 재시도는 안 한다."""
    llm = ClaudeCodeCliLLM()
    overflow = _result_line(
        is_error=True,
        result="API Error: 400 A maximum of 4 blocks with cache_control may be provided. Found 5.",
    )
    other = _result_line(is_error=True, result="다른 이유로 여전히 실패")
    responses = [overflow, other]

    async def fake_exec(*_args: str, **_kwargs: object) -> AsyncMock:
        return _mock_proc(responses.pop(0))

    with (
        patch.object(module.asyncio, "create_subprocess_exec", fake_exec),
        pytest.raises(LLMExecutionError, match="다른 이유"),
    ):
        await llm.complete("변하는 접미어", cache_prefix="안정적인 접두어")


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class _FakeStdout:
    """`readline()`이 미리 채워둔 줄을 하나씩 내준다 — turn() 은 stdin 을 쓸 때마다 그만큼만

    stdout 을 읽으므로, 호출 순서대로 큐에 다 채워두면 된다(§ test_claude_code_cli_llm.py)."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _FakeTurnProc:
    def __init__(self, stdout_lines: list[bytes]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self.returncode: int | None = None
        self.killed = False
        self.waited = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int | None:
        self.waited = True
        return self.returncode


def _turn_result_line(**overrides: object) -> bytes:
    return _result_line(**overrides) + b"\n"


async def test_turn_rejects_instances_without_allow_slash_commands():
    llm = ClaudeCodeCliLLM()  # 기본값 False
    with pytest.raises(RuntimeError, match="allow_slash_commands"):
        llm.turn()


async def test_turn_drops_disable_slash_commands_flag():
    llm = ClaudeCodeCliLLM(allow_slash_commands=True)
    captured: list[str] = []

    async def fake_exec(*args: str, **_kwargs: object) -> _FakeTurnProc:
        captured.extend(args)
        return _FakeTurnProc([_turn_result_line()])

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        async with llm.turn() as turn_llm:
            await turn_llm.complete("prompt")

    assert "--disable-slash-commands" not in captured
    assert "--strict-mcp-config" in captured  # 나머지 잠금은 그대로 유지


async def test_turn_reuses_one_process_and_sends_clear_between_calls():
    llm = ClaudeCodeCliLLM(allow_slash_commands=True)
    spawn_count = 0

    async def fake_exec(*_args: str, **_kwargs: object) -> _FakeTurnProc:
        nonlocal spawn_count
        spawn_count += 1
        # call1 결과 → /clear 결과 → call2 결과, 순서대로 한 프로세스가 다 내준다.
        return _FakeTurnProc(
            [
                _turn_result_line(result="첫번째"),
                _turn_result_line(result=None),  # /clear 응답
                _turn_result_line(result="두번째"),
            ]
        )

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        async with llm.turn() as turn_llm:
            first = await turn_llm.complete("prompt1")
            second = await turn_llm.complete("prompt2")

    assert first == "첫번째"
    assert second == "두번째"
    assert spawn_count == 1  # 프로세스는 한 번만 떴다

    proc = turn_llm._proc
    written = [json.loads(b) for b in proc.stdin.written]
    assert len(written) == 3
    assert written[0]["message"]["content"][0]["text"] == "prompt1"
    assert written[1]["message"]["content"][0]["text"] == "/clear"
    assert written[2]["message"]["content"][0]["text"] == "prompt2"


async def test_turn_kills_the_process_on_exit():
    llm = ClaudeCodeCliLLM(allow_slash_commands=True)
    proc_holder: list[_FakeTurnProc] = []

    async def fake_exec(*_args: str, **_kwargs: object) -> _FakeTurnProc:
        proc = _FakeTurnProc([_turn_result_line()])
        proc_holder.append(proc)
        return proc

    with patch.object(module.asyncio, "create_subprocess_exec", fake_exec):
        async with llm.turn() as turn_llm:
            await turn_llm.complete("prompt")

    assert proc_holder[0].killed
    assert proc_holder[0].waited


async def test_turn_error_envelope_raises_execution_error():
    llm = ClaudeCodeCliLLM(allow_slash_commands=True)

    async def fake_exec(*_args: str, **_kwargs: object) -> _FakeTurnProc:
        return _FakeTurnProc([_turn_result_line(is_error=True, result="뭔가 실패")])

    with (
        patch.object(module.asyncio, "create_subprocess_exec", fake_exec),
        pytest.raises(LLMExecutionError),
    ):
        async with llm.turn() as turn_llm:
            await turn_llm.complete("prompt")


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
