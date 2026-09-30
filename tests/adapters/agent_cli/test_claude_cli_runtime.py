"""ClaudeCliAgentRuntime — 가짜 CLI(stdout 대역)로 격리 인자·transcript·장애 시그니처 (§A6).

멈춤·정리는 test_claude_cli_lifecycle.py, 실제 `claude` 는 test_claude_cli_native.py.
"""

import asyncio

import pytest

from auto_apply.contracts.agent import AgentEnd, AgentLimits
from auto_apply.domain.errors import LLMAuthRequired, LLMExecutionError, LLMQuotaExceeded
from auto_apply.domain.failure import FailureKind, classify_failure
from auto_apply.services.run_tokens import RunTokens
from tests.adapters.agent_cli.fake_kit import (
    TOOLS,
    Calls,
    all_gone,
    exists,
    runtime,
    same_dir,
    seen,
)


async def test_cli_is_isolated_to_our_mcp_tools(tmp_path, out, monkeypatch):
    tokens = RunTokens()
    rt = runtime(tmp_path, tokens, "ok", monkeypatch)
    await rt.run("시스템 지시", TOOLS, Calls(), limits=AgentLimits(), run_id="run_1")

    s = seen(out)
    argv = s["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # 내장 도구 0개
    assert "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert allowed == ["mcp__auto_apply__navigate", "mcp__auto_apply__snapshot"]
    assert s["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert s["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert s["env"]["MCP_TOOL_TIMEOUT"] == str(int((5.0 + 60) * 1000))  # ask_user 대기보다 길게
    workdir = tmp_path / "runs" / "run_1"
    assert same_dir(s["cwd"], workdir)
    assert s["system_prompt"] == "시스템 지시"
    assert not exists(workdir / "system_prompt.md")  # 끝나면 지운다

    token = s["env"]["AUTO_APPLY_RUN_TOKEN"]
    config = (workdir / "mcp.json").read_text(encoding="utf-8")
    assert token not in config and "${AUTO_APPLY_RUN_TOKEN}" in config  # 비밀은 env 로만
    assert token not in " ".join(argv)
    assert tokens.live_count == 0 and tokens.resolve(token) is None  # run 이 끝나면 폐기


async def test_transcript_hides_identifiers_and_run_token(tmp_path, out, monkeypatch):
    rt = runtime(tmp_path, RunTokens(), "ok", monkeypatch)
    outcome = await rt.run("s", TOOLS, Calls(), limits=AgentLimits(), run_id="run_1")

    assert outcome.ended is AgentEnd.COMPLETED and outcome.text == "끝"
    assert (outcome.input_tokens, outcome.output_tokens) == (15, 7)
    transcript = (tmp_path / "runs" / "run_1" / "transcript.jsonl").read_text(encoding="utf-8")
    assert "900101-1234567" not in transcript and "[주민등록번호 가림]" in transcript
    assert seen(out)["env"]["AUTO_APPLY_RUN_TOKEN"] not in transcript


@pytest.mark.parametrize(
    ("mode", "error", "kind"),
    [("auth", LLMAuthRequired, FailureKind.FATAL), ("quota", LLMQuotaExceeded, FailureKind.FATAL)],
)
async def test_login_and_quota_signatures_are_infra_failures(
    tmp_path, out, monkeypatch, mode, error, kind
):
    rt = runtime(tmp_path, RunTokens(), mode, monkeypatch)
    with pytest.raises(error) as caught:
        await rt.run("s", TOOLS, Calls(), limits=AgentLimits())
    assert classify_failure(caught.value) is kind  # 도메인 결과가 아니라 예외 → §A9 정책


async def test_exit_without_result_is_execution_error_with_stderr(tmp_path, out, monkeypatch):
    rt = runtime(tmp_path, RunTokens(), "crash", monkeypatch)
    with pytest.raises(LLMExecutionError, match="boom") as caught:
        await rt.run("s", TOOLS, Calls(), limits=AgentLimits())
    assert classify_failure(caught.value) is FailureKind.TRANSIENT


async def test_foreign_builtin_tool_stops_the_run(tmp_path, out, monkeypatch):
    """`--tools ""` 가 안 먹는 CLI 라도 Bash 가 보이면 도구를 열지 않고 멈춘다(닫힌 쪽)."""
    tokens, calls = RunTokens(), Calls()
    rt = runtime(tmp_path, tokens, "foreign", monkeypatch)
    with pytest.raises(LLMExecutionError, match="Bash"):
        await asyncio.wait_for(rt.run("s", TOOLS, calls, limits=AgentLimits()), 10)
    assert calls.calls == [] and tokens.live_count == 0
    if exists(out / "pids"):
        await all_gone(out)
