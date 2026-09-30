"""Anthropic Messages API 로 도는 AgentRuntime (§A6, `LLM_PROVIDER=anthropic`).

tool-use 루프를 여기서 직접 돈다 — 도구는 MCP 를 거치지 않고 호출자가 넘긴 `call_tool` 을 같은
프로세스에서 부른다. 모델이 가진 도구는 `tools` 로 넘긴 것뿐이다(서버 도구·내장 도구 없음) — 승인
API 를 부르거나 파일을 읽을 통로가 없다(§A4). 한도·done·시간 계약은 CLI 런타임과 같다:
넘친 호출은 도구에 닿지 않고, done 을 받으면 더 부르지 않으며, 시간이 다 되면 진행 중인
호출째 끊는다.
벤더 타입은 이 모듈 밖으로 나가지 않는다.
"""

import asyncio
import json
import re
import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam, ToolParam, ToolResultBlockParam

from auto_apply.adapters.agent.claude_cli_stream import Transcript
from auto_apply.adapters.llm.anthropic import api_errors
from auto_apply.contracts.agent import AgentEnd, AgentLimits, AgentOutcome, AgentTool
from auto_apply.domain.errors import LLMAuthRequired
from auto_apply.ports.agent import CallTool

log = structlog.get_logger(__name__)

KICKOFF = "시스템 지시대로 지금 시작하라. 도구로만 일한다."
# 비스트리밍 요청이 SDK HTTP 타임아웃 안에 끝나는 크기. 도구 호출 한 번 분량이면 충분하다.
MAX_TOKENS = 16000
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class AnthropicApiAgentRuntime:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        runs_dir: Path,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key, self._model, self._runs_dir = api_key, model, runs_dir
        # SDK 가 429·5xx·연결 오류를 retry-after 로 다시 한다 — 그래도 안 되면 §A9 가 가른다
        self._max_retries = max_retries
        self._transport = transport  # 테스트 전송(가짜 API). None 이면 SDK 기본

    async def run(
        self,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        if not self._api_key:  # 네트워크에 가기 전에 — 재시도로 안 풀린다(FATAL)
            raise LLMAuthRequired("LLM_PROVIDER=anthropic 인데 ANTHROPIC_API_KEY 가 없다")
        name = run_id if run_id and _SAFE_ID.fullmatch(run_id) else f"agent_{secrets.token_hex(6)}"
        loop = _Loop(self._model, system_prompt, tools, call_tool, limits)
        http = httpx.AsyncClient(transport=self._transport) if self._transport else None
        # 끝·예외·취소 어느 쪽이든 연결을 닫는다
        async with AsyncAnthropic(
            api_key=self._api_key, max_retries=self._max_retries, http_client=http
        ) as client:
            with self._transcript(name) as transcript:
                outcome = await loop.drive(client, transcript)
        log.info(
            "agent.api_finished", ended=str(outcome.ended), tool_calls=outcome.tool_calls,
            application_id=None, run_id=name,
        )  # fmt: skip
        return outcome

    def _transcript(self, name: str) -> Transcript:
        workdir = self._runs_dir / name
        workdir.mkdir(parents=True, exist_ok=True)
        return Transcript(workdir / "transcript.jsonl", secrets=(self._api_key,))


class _Loop:
    """run 한 번의 대화 상태 — 요청을 보내고, tool_use 를 차례로 `call_tool` 에 넘긴다."""

    def __init__(
        self,
        model: str,
        system_prompt: str,
        tools: Sequence[AgentTool],
        call_tool: CallTool,
        limits: AgentLimits,
    ) -> None:
        self._model, self._system, self._call, self._limits = (
            model,
            system_prompt,
            call_tool,
            limits,
        )
        self._tools: list[ToolParam] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        self._messages: list[MessageParam] = [{"role": "user", "content": KICKOFF}]
        self.calls = self.input_tokens = self.output_tokens = 0
        self.text = ""

    async def drive(self, client: AsyncAnthropic, transcript: Transcript) -> AgentOutcome:
        deadline = asyncio.timeout(self._limits.max_seconds)
        try:
            async with deadline:
                return await self._turns(client, transcript)
        except TimeoutError:
            if not deadline.expired():  # 도구 안에서 난 TimeoutError 는 한도가 아니다
                raise
            return self._outcome(AgentEnd.TIME_LIMIT)

    async def _turns(self, client: AsyncAnthropic, transcript: Transcript) -> AgentOutcome:
        while True:
            resp = await self._ask(client)
            transcript.write(resp.model_dump_json().encode())
            uses = [b for b in resp.content if b.type == "tool_use"]
            # tool_use 로 멈춘 게 아니면(end_turn·max_tokens·refusal) 에이전트가 스스로 멈춘
            # 것이다 — max_tokens 에 잘린 tool_use 입력은 반쪽일 수 있어 실행하지 않는다
            if resp.stop_reason != "tool_use" or not uses:
                return self._outcome(AgentEnd.COMPLETED)
            results: list[ToolResultBlockParam] = []
            for use in uses:  # 브라우저 동작이라 순서대로, 하나씩
                if self.calls >= self._limits.max_tool_calls:
                    return self._outcome(AgentEnd.TOOL_LIMIT)
                self.calls += 1
                args = use.input if isinstance(use.input, dict) else {}
                reply = await self._call(use.name, args)
                transcript.write(_result_line(use.id, use.name, reply.ok, reply.content))
                if reply.done:
                    return self._outcome(AgentEnd.COMPLETED)
                results.append(
                    {"type": "tool_result", "tool_use_id": use.id,
                     "content": reply.content, "is_error": not reply.ok}
                )  # fmt: skip
            # 한 응답의 tool_result 는 한 user 메시지로 — 나누면 병렬 호출 습관이 깨진다.
            # assistant 내용은 받은 그대로 되돌린다(thinking 블록은 손대면 거부된다)
            self._messages.append({"role": "assistant", "content": resp.content})
            self._messages.append({"role": "user", "content": results})

    async def _ask(self, client: AsyncAnthropic) -> Message:
        with api_errors():
            resp = await client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=self._tools,
                messages=self._messages,
                # 자라는 대화의 마지막 블록까지 캐시 — 매 턴 시스템 프롬프트·도구·앞 대화를
                # 다시 읽지 않게
                cache_control={"type": "ephemeral"},
            )
        self.input_tokens += resp.usage.input_tokens
        self.output_tokens += resp.usage.output_tokens
        self.text = "".join(b.text for b in resp.content if b.type == "text")
        return resp

    def _outcome(self, ended: AgentEnd) -> AgentOutcome:
        return AgentOutcome(
            ended=ended,
            tool_calls=self.calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            text=self.text,
        )


def _result_line(use_id: str, name: str, ok: bool, content: str) -> bytes:
    line: dict[str, Any] = {
        "type": "tool_result", "tool_use_id": use_id, "name": name, "ok": ok, "content": content,
    }  # fmt: skip
    return json.dumps(line, ensure_ascii=False).encode()
