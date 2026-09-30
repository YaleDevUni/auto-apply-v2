"""가짜 CLI(fake_claude.py)로 ClaudeCliAgentRuntime 을 도는 테스트 공용 (§A6)."""

import asyncio
import json
import os
import sys
from pathlib import Path

from auto_apply.adapters.agent.claude_cli import ClaudeCliAgentRuntime
from auto_apply.contracts.agent import AgentTool, ToolReply
from auto_apply.services.run_tokens import RunTokens

FAKE = Path(__file__).with_name("fake_claude.py")
TOOLS = (
    AgentTool(name="navigate", description="", input_schema={"type": "object"}),
    AgentTool(name="snapshot", description="", input_schema={"type": "object"}),
)


class Calls:
    def __init__(self, *, done: bool = False) -> None:
        self.calls: list[str] = []
        self._done = done

    async def __call__(self, name, args) -> ToolReply:
        self.calls.append(name)
        return ToolReply(ok=True, content="{}", done=self._done)


def runtime(tmp_path: Path, tokens: RunTokens, mode: str, monkeypatch) -> ClaudeCliAgentRuntime:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    return ClaudeCliAgentRuntime(
        tokens.open,
        mcp_url="http://127.0.0.1:9/mcp",
        runs_dir=tmp_path / "runs",
        human_wait_s=5.0,
        command=(sys.executable, str(FAKE)),
    )


def seen(out: Path) -> dict:
    return json.loads((out / "seen.json").read_text(encoding="utf-8"))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def all_gone(out: Path) -> None:
    pids = [int(p) for p in (out / "pids").read_text().split()]
    for _ in range(40):  # 손자 프로세스는 init 이 거둔다 — 잠깐 기다린다
        if not any(alive(p) for p in pids):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"남은 프로세스: {[p for p in pids if alive(p)]}")


def same_dir(a: str, b: Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)  # macOS /var → /private/var


def exists(path: Path) -> bool:
    return path.exists()


async def wait_for(path: Path) -> None:
    for _ in range(200):
        if exists(path):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{path} 가 생기지 않았다")
