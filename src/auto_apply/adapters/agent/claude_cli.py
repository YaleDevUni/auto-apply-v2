"""`claude` CLI 로 도는 AgentRuntime — 기본 런타임 (D5, §A6).

로그인된 Claude Code 구독에 올라탄다(`--bare` 는 API 키를 강제해 못 쓴다 — `ClaudeCodeCliLLM`).
에이전트가 가진 것은 **우리 MCP 도구뿐**이다: 내장 도구 0개(`--tools ""`), MCP 는 run 별 설정
파일의 우리 서버 하나(`--strict-mcp-config`), 허용 목록도 그 도구 이름뿐(`dontAsk` — 나머지는
묻지 않고 거부). 승인 API 를 부르거나 파일을 읽을 통로가 없다(§A4). 설정 원천도 끊는다: 작업
디렉터리는 run 디렉터리, 사용자·프로젝트 settings(훅)·CLAUDE.md·auto-memory·슬래시 커맨드를
읽지 않는다 — 실측(2.1.285): 저장소 안 cwd 에서 auto-memory·CLAUDE.md 가 6.5k 토큰 끼어들고,
아래 env 두 개로 사라진다.

도구 호출은 MCP → run 토큰 → 여기서 감싼 `call_tool` 로 온다(§A5). `done` 은 MCP 로 가지 않으므로
감싼 쪽이 보고 프로세스를 멈춘다. 한도도 감싼 쪽이 센다 — 넘친 호출은 도구에 닿지 않는다.
토큰은 설정 파일에 `${AUTO_APPLY_RUN_TOKEN}` 로만 적고 값은 자식 env 로 준다(디스크·argv 에 없게).
"""

import asyncio
import json
import os
import re
import secrets
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import structlog

from auto_apply.adapters.agent.claude_cli_process import drain_tail, spawn, stop
from auto_apply.adapters.agent.claude_cli_stream import (
    MCP_SERVER,
    Transcript,
    Usage,
    mcp_tool_name,
    raise_if_failed,
    read_stream,
)
from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentOutcome, AgentTool, ToolReply
from auto_apply.ports.agent import CallTool

log = structlog.get_logger(__name__)

OpenToken = Callable[[CallTool], AbstractAsyncContextManager[str]]
KICKOFF = "시스템 지시대로 지금 시작하라. 도구로만 일한다."
TOKEN_ENV = "AUTO_APPLY_RUN_TOKEN"
# ask_user 는 사람 답을 최대 human_wait_s 기다린다 — CLI 가 그보다 먼저 도구를 끊지 않게
TOOL_TIMEOUT_MARGIN_S = 60.0
_ISOLATION_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class _Gate:
    """MCP 로 온 호출의 관문 — 기동 점검 전 거부, 한도 계수, done·한도에서 멈춤 신호."""

    def __init__(self, call_tool: CallTool, limits: AgentLimits) -> None:
        self._call, self._limits = call_tool, limits
        self._armed = False  # init 점검(우리 도구뿐)을 통과해야 연다
        self.calls = 0
        self.over_limit = False
        self.stopped = asyncio.Event()

    def arm(self) -> None:
        self._armed = True

    async def __call__(self, name: str, args: object) -> ToolReply:
        if not self._armed or self.stopped.is_set():
            return _refuse("not_running", done=self.stopped.is_set())
        if self.calls >= self._limits.max_tool_calls:
            self.over_limit = True
            self.stopped.set()
            return _refuse("tool_limit", done=True)
        self.calls += 1
        reply = await self._call(name, args)  # type: ignore[arg-type]
        if reply.done:
            self.stopped.set()
        return reply


def _refuse(error: str, *, done: bool) -> ToolReply:
    return ToolReply(ok=False, content=json.dumps({"error": error}), done=done)


class ClaudeCliAgentRuntime:
    def __init__(
        self,
        open_token: OpenToken,
        *,
        mcp_url: str,
        runs_dir: Path,
        human_wait_s: float,
        command: Sequence[str] = ("claude",),
        model: str = "claude-sonnet-5",
        max_budget_usd: float | None = None,
    ) -> None:
        self._open_token, self._mcp_url, self._runs_dir = open_token, mcp_url, runs_dir
        self._tool_timeout_ms = int((human_wait_s + TOOL_TIMEOUT_MARGIN_S) * 1000)
        # 실행 파일(과 앞 인자) — 테스트는 (python, 가짜 CLI 스크립트) 로 바꾼다
        self._command, self._model, self._budget = tuple(command), model, max_budget_usd

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        name = run_id if run_id and _SAFE_ID.fullmatch(run_id) else f"agent_{secrets.token_hex(6)}"
        workdir = self._runs_dir / name
        workdir.mkdir(parents=True, exist_ok=True)
        gate = _Gate(call_tool, limits)
        async with self._open_token(gate) as token:
            return await self._drive(workdir, system_prompt, tools, gate, token, limits)

    async def _drive(
        self,
        workdir: Path,
        system_prompt: str,
        tools: Sequence[AgentTool],
        gate: _Gate,
        token: str,
        limits: AgentLimits,
    ) -> AgentOutcome:
        allowed = frozenset(mcp_tool_name(t.name) for t in tools)
        prompt_file = workdir / "system_prompt.md"
        prompt_file.write_text(system_prompt, encoding="utf-8")
        env = {**os.environ, **_ISOLATION_ENV, TOKEN_ENV: token}
        env["MCP_TOOL_TIMEOUT"] = str(self._tool_timeout_ms)
        try:
            proc = await spawn(self._args(workdir, prompt_file, allowed), cwd=workdir, env=env)
        except BaseException:
            prompt_file.unlink(missing_ok=True)
            raise
        stderr = asyncio.create_task(drain_tail(proc.stderr))
        usage = Usage()
        with Transcript(workdir / "transcript.jsonl", secrets=(token,)) as transcript:
            reader = asyncio.create_task(
                read_stream(proc.stdout, transcript, allowed, usage, on_checked=gate.arm)
            )
            stopper = asyncio.create_task(gate.stopped.wait())
            ended = AgentEnd.COMPLETED
            try:
                try:
                    async with asyncio.timeout(limits.max_seconds):
                        await asyncio.wait({reader, stopper}, return_when=asyncio.FIRST_COMPLETED)
                except TimeoutError:
                    ended = AgentEnd.TIME_LIMIT
                if gate.over_limit:
                    ended = AgentEnd.TOOL_LIMIT
                result = None
                if reader.done() and not gate.stopped.is_set():  # 에이전트가 스스로 끝냈다
                    result = reader.result()  # init 점검 실패는 여기서 올라간다
                    await proc.wait()
                    raise_if_failed(result, proc.returncode, await stderr)
            finally:
                for task in (reader, stopper):
                    task.cancel()
                await stop(proc)
                stderr.cancel()
                prompt_file.unlink(missing_ok=True)  # 프로필 요약이 담겨 있다 — 오래 두지 않는다
        log.info(
            "agent.cli_finished", ended=str(ended), tool_calls=gate.calls,
            application_id=None, run_id=workdir.name,
        )  # fmt: skip
        return AgentOutcome(
            ended=ended,
            tool_calls=gate.calls,
            input_tokens=result.input_tokens if result else usage.input_tokens,
            output_tokens=result.output_tokens if result else usage.output_tokens,
            text=result.text if result else "",
        )

    def _args(self, workdir: Path, prompt_file: Path, allowed: frozenset[str]) -> list[str]:
        config = workdir / "mcp.json"
        server = {
            "type": "http",
            "url": self._mcp_url,
            "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
        }
        config.write_text(json.dumps({"mcpServers": {MCP_SERVER: server}}), encoding="utf-8")
        args = [
            *self._command, "-p", KICKOFF,
            "--output-format", "stream-json", "--verbose",
            "--model", self._model,
            "--system-prompt-file", str(prompt_file),
            "--tools", "",
            "--mcp-config", str(config), "--strict-mcp-config",
            "--allowedTools", ",".join(sorted(allowed)),
            "--permission-mode", "dontAsk",
            "--disable-slash-commands",
            "--setting-sources", "",
            "--no-session-persistence",
        ]  # fmt: skip
        if self._budget is not None:
            args += ["--max-budget-usd", str(self._budget)]
        return args
