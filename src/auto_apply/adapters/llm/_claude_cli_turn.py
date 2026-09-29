"""`ClaudeCodeCliLLM.turn()`이 반환하는 영속 프로세스 세션.

`claude_code_cli.py` 모듈 docstring의 "turn()" 절 참고 — 한 논리적 대화 턴(예: 대화형
에이전트의 ReAct 루프) 동안 프로세스 하나를 계속 살려서 매 호출의 프로세스 기동 비용을
최초 1회로 줄인다. 두 번째 호출부터는 실제 프롬프트 앞에 `/clear`를 먼저 보내 이전 호출의
대화 맥락을 지운다 — 프로세스는 재사용하되 대화는 재사용하지 않는다.

`claude_code_cli.py`와 서로 참조해서(이 파일이 그쪽의 헬퍼를, 그쪽이 이 파일의 클래스를 씀)
`claude_code_cli.py`는 이 파일을 함수 안에서 지연 import 한다 — 순환 import 방지.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from auto_apply.adapters.llm.claude_code_cli import (
    _build_stdin_payload,
    _classify_error,
    logger,
)
from auto_apply.domain.errors import LLMExecutionError, LLMSchemaViolation

if TYPE_CHECKING:
    from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM

# /clear 는 슬래시커맨드라 --tools ""/--strict-mcp-config 와 무관하게 LLM 호출 없이 로컬에서
# 처리된다(실측: duration_ms 없이 30ms, 새 session_id로 전환) — 매번 새로 만들 필요 없이
# 상수로 둔다.
_CLEAR_MESSAGE = {
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": "/clear"}]},
}
_CLEAR_PAYLOAD = (json.dumps(_CLEAR_MESSAGE) + "\n").encode()


class ClaudeCliTurn:
    """`ports/llm.py` `LLMClient.turn()`이 요구하는 async context manager.

    `complete`/`structured`는 `ClaudeCodeCliLLM`과 같은 시그니처다 — 호출하는 쪽은
    `llm.structured(...)` 대신 `turn_llm.structured(...)`만 바꾸면 된다.
    `cache_control` 4개 초과 폴백(`claude_code_cli.py`의 `_CACHE_OVERFLOW_PATTERN`)은 여기선
    안 한다 — 짧은 대화형 프롬프트 용도라 그 한도에 걸릴 크기가 아니다. 걸리면 일반
    `LLMExecutionError`로 분류된다.
    """

    def __init__(self, owner: "ClaudeCodeCliLLM") -> None:
        self._owner = owner
        self._proc: asyncio.subprocess.Process | None = None
        self._json_schema: dict[str, Any] | None = None
        self._step = 0

    async def __aenter__(self) -> "ClaudeCliTurn":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        # 다음 턴으로 대화가 새면 안 되니 항상 죽인다 — 정상 종료를
        # 기다려주는 프로토콜이 없어(그럴 이유도 없다, 턴이 끝나면 이 프로세스는 볼 일이 없다).
        if self._proc is None:
            return
        if self._proc.returncode is None:
            self._proc.kill()
        await self._proc.wait()

    async def complete(self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = "") -> str:
        envelope = await self._call(prompt, cache_prefix=cache_prefix, json_schema=None)
        result = envelope.get("result")
        if not isinstance(result, str):
            raise LLMExecutionError(f"claude CLI 응답에 result 가 없다: {envelope}")
        return result

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T:
        envelope = await self._call(
            prompt, cache_prefix=cache_prefix, json_schema=schema.model_json_schema()
        )
        payload = envelope.get("structured_output")
        if payload is None:
            raise LLMSchemaViolation(f"{schema.__name__}: structured_output 이 없다: {envelope}")
        try:
            return schema.model_validate(payload)
        except ValidationError as e:
            raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e

    async def _call(
        self, prompt: str, *, cache_prefix: str, json_schema: dict[str, Any] | None
    ) -> dict[str, Any]:
        # --json-schema 는 프로세스 기동 시 고정되는 CLI 플래그라 호출마다 바꿀 수 없다 —
        # 첫 호출에서 스키마를 확정해 프로세스를 띄우고, 스키마가 바뀌면(이 턴 안에서
        # complete()/structured() 를 섞어 쓰는 드문 경우) 재사용을 포기하고 새로 띄운다.
        if self._proc is None:
            self._proc = await self._owner._spawn(json_schema)
            self._json_schema = json_schema
        elif json_schema != self._json_schema:
            if self._proc.returncode is None:
                self._proc.kill()
            await self._proc.wait()
            self._proc = await self._owner._spawn(json_schema)
            self._json_schema = json_schema
        else:
            await self._clear()

        self._step += 1
        return await self._exchange(prompt, cache_prefix)

    async def _clear(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        proc.stdin.write(_CLEAR_PAYLOAD)
        await proc.stdin.drain()
        await self._read_result()  # /clear 자체도 result 줄 하나로 끝난다 — 드레인만 한다.

    async def _exchange(self, prompt: str, cache_prefix: str) -> dict[str, Any]:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        proc.stdin.write(_build_stdin_payload(prompt, cache_prefix))
        await proc.stdin.drain()
        envelope = await self._read_result()
        if envelope.get("is_error"):
            raise _classify_error(envelope)

        usage = envelope.get("usage", {})
        logger.info(
            "claude_code_cli_call",
            cache_prefix_len=len(cache_prefix),
            reused_process=True,
            turn_step=self._step,
            cost_usd=envelope.get("total_cost_usd"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        )
        return envelope

    async def _read_result(self) -> dict[str, Any]:
        """stdout 을 줄 단위로 읽다 `"type":"result"` 를 만나면 그 자리에서 멈춘다.

        일회성 호출의 `_find_result_line`(전체 stdout 을 다 받은 뒤 훑는다)과 달리, 프로세스가
        살아있는 채로 다음 메시지를 더 받아야 하니 필요한 줄만큼만 읽고 리더를 계속 열어둔다.
        """
        proc = self._proc
        assert proc is not None and proc.stdout is not None

        async def _read() -> dict[str, Any]:
            while True:
                line = await proc.stdout.readline()  # type: ignore[union-attr]
                if not line:
                    raise LLMExecutionError("claude CLI 프로세스가 응답 없이 종료됐다")
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "result":
                    return obj

        try:
            return await asyncio.wait_for(_read(), timeout=self._owner._timeout_seconds)
        except TimeoutError as e:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise LLMExecutionError(f"claude CLI 타임아웃({self._owner._timeout_seconds}s)") from e
