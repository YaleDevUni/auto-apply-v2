"""`claude --output-format stream-json` 줄 읽기 — 기동 점검·결과·transcript (§A6).

기동(init) 이벤트에서 모델이 볼 도구가 **우리 MCP 도구뿐인지** 확인하고 아니면 run 을 멈춘다 —
CLI 버전이 바뀌어 `--tools ""` 가 먹지 않는 날에도 Bash·Read·WebFetch 로 승인 API·파일에 닿지 않게
(닫힌 쪽). transcript 는 줄마다 고유식별정보를 가린 뒤 쓴다(절대 규칙 5).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from auto_apply.adapters.llm.claude_code_cli import _classify_error
from auto_apply.domain.errors import LLMExecutionError
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers

MCP_SERVER = "auto_apply"
TOOL_PREFIX = f"mcp__{MCP_SERVER}__"


def mcp_tool_name(name: str) -> str:
    return TOOL_PREFIX + name


def check_init(event: dict[str, Any], allowed: frozenset[str]) -> None:
    """모델에게 보이는 도구가 `allowed`(우리 MCP 도구 이름) 안에만 있고, 우리 서버가 붙었는지."""
    tools = event.get("tools")
    if not isinstance(tools, list):
        raise LLMExecutionError("claude CLI init 에 도구 목록이 없다")
    foreign = sorted(str(t) for t in tools if str(t) not in allowed)
    if foreign:
        raise LLMExecutionError(f"claude CLI 에 우리 것이 아닌 도구가 열려 있다: {foreign}")
    listed = event.get("mcp_servers") or []
    servers = {s.get("name"): s.get("status") for s in listed if isinstance(s, dict)}
    if servers.get(MCP_SERVER) != "connected":
        raise LLMExecutionError(f"claude CLI 가 MCP 에 붙지 못했다: {servers.get(MCP_SERVER)}")
    if set(servers) != {MCP_SERVER}:
        raise LLMExecutionError(f"claude CLI 에 다른 MCP 서버가 있다: {sorted(map(str, servers))}")


@dataclass
class Result:
    input_tokens: int = 0
    output_tokens: int = 0
    text: str = ""
    error: LLMExecutionError | None = None


def read_result(event: dict[str, Any]) -> Result:
    usage = event.get("usage") or {}
    tokens_in = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    error = _classify_error(event) if event.get("is_error") else None
    return Result(
        input_tokens=tokens_in,
        output_tokens=int(usage.get("output_tokens") or 0),
        text=str(event.get("result") or "") if error is None else "",
        error=error,
    )


def parse(line: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


class Transcript:
    """run 디렉터리의 `transcript.jsonl`. 비밀(run 토큰)과 주민등록번호 꼴은 가려서 쓴다."""

    def __init__(self, path: Path, *, secrets: tuple[str, ...]) -> None:
        self.path = path
        self._secrets = tuple(s for s in secrets if s)
        self._fh: IO[str] | None = None

    def __enter__(self) -> "Transcript":
        self._fh = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def write(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text.strip() or self._fh is None:
            return
        for secret in self._secrets:
            text = text.replace(secret, "[가림]")
        self._fh.write(redact_resident_registration_numbers(text)[0] + "\n")
        self._fh.flush()


class Usage:
    """결과 줄 없이 끊었을 때(done·한도) 쓸 토큰 — 메시지 id 별 마지막 사용량의 합."""

    def __init__(self) -> None:
        self._by_message: dict[str, tuple[int, int]] = {}

    def add(self, event: dict[str, object]) -> None:
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return
        partial = read_result({"usage": message["usage"]})
        self._by_message[str(message.get("id"))] = (partial.input_tokens, partial.output_tokens)

    @property
    def input_tokens(self) -> int:
        return sum(i for i, _ in self._by_message.values())

    @property
    def output_tokens(self) -> int:
        return sum(o for _, o in self._by_message.values())


async def read_stream(
    stdout: asyncio.StreamReader | None,
    transcript: Transcript,
    allowed: frozenset[str],
    usage: Usage,
    *,
    on_checked: Callable[[], None],
) -> Result | None:
    """끝까지 읽어 결과 줄을 돌려준다. init 점검을 통과하면 `on_checked` — 그 전엔 도구가 닫힌다."""
    result = None
    async for line in _lines(stdout):
        transcript.write(line)
        event = parse(line)
        if event is None:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            check_init(event, allowed)
            on_checked()
        elif kind == "assistant":
            usage.add(event)
        elif kind == "result":
            result = read_result(event)
    return result


async def _lines(stdout: asyncio.StreamReader | None) -> AsyncIterator[bytes]:
    if stdout is None:
        return
    try:
        async for line in stdout:
            yield line
    except ValueError as exc:  # 한 줄이 버퍼 한도를 넘었다
        raise LLMExecutionError("claude CLI 출력 한 줄이 너무 길다") from exc


def raise_if_failed(result: Result | None, returncode: int | None, stderr: bytes) -> None:
    if result is not None and result.error is not None:
        raise result.error  # 로그인 풀림·한도초과는 여기서 LLMAuthRequired·LLMQuotaExceeded
    if result is None:
        tail = stderr.decode(errors="replace")
        raise LLMExecutionError(f"claude CLI 가 결과 없이 끝났다(종료 코드 {returncode}): {tail}")
