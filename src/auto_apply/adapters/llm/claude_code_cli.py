"""Claude Code CLI(`claude`)를 subprocess 로 구동하는 LLMClient 구현 (ARCHITECTURE.md §11.2).

`ANTHROPIC_API_KEY` 종량제 대신, 이 머신에 로그인된 Claude Code 구독(OAuth)에 올라탄다.
`--bare` 모드는 일부러 안 쓴다 — `--bare`는 OAuth/keychain 을 아예 읽지 않고
`ANTHROPIC_API_KEY`(또는 `apiKeyHelper`)를 강제해서, 이 어댑터가 피하려는 바로 그 종량제
과금으로 되돌아간다(실측: `claude --bare --help`). 대신 하네스를 낱개 플래그로 걷어낸다:
빌트인 툴 0개(`--tools ""`), MCP 서버 0개(`--strict-mcp-config`, `--mcp-config` 생략),
스킬 비활성(`--disable-slash-commands`), 프로젝트/사용자 settings.json·CLAUDE.md 무시
(`--setting-sources ""`), 기본 시스템 프롬프트를 우리 전용 문장으로 완전히 교체
(`--system-prompt`). `--model` 은 항상 명시한다 — 생략하면 CLI 가 모델 라우팅용 분류기 호출을
Haiku 로 한 번 더 태운다(실측: `--model` 없이 부르면 `modelUsage`에 `claude-haiku-4-5`가
잡히고, 명시하면 사라진다).

캐시(§ 사용자 요청 "cache 적극 활용"): 완전히 새 프로세스로 매번 호출하면(세션 없음)
프롬프트가 100% 동일해도 캐시가 전혀 안 붙는다(실측: 동일 system-prompt 로 두 번 연속 호출
— `cache_creation_input_tokens`/`cache_read_input_tokens` 둘 다 0). Claude Code 는 세션을
이어야만(`--resume`) 이전 턴을 캐시로 읽는다(실측: 세션을 잇자 두 번째 턴에서
`cache_read_input_tokens` 가 첫 턴의 `cache_creation_input_tokens` 와 정확히 일치). 그래서
`cache_key`(호출자가 주는 힌트, 보통 user_id)별로 세션 id 를 들고 있다가 같은 키의 다음
호출에 `--resume` 한다 — 같은 사용자의 Fact/Profile 목록처럼 여러 공고에 걸쳐 반복되는 큰
프리픽스가 캐시로 읽힌다. 무한정 이어붙이면 세션이 계속 자라 캐시 이득보다 비용이 커지므로
`_MAX_TURNS_PER_SESSION`·`_SESSION_TTL_SECONDS` 를 넘기면 새 세션을 판다. 같은 키로 동시에
두 호출이 들어오면 같은 세션 파일에 동시 쓰기가 나므로 키별 `asyncio.Lock`으로 직렬화한다.

전제: 이 프로세스를 실행하는 머신에 `claude` CLI 가 설치되고 `claude login`(또는
`claude setup-token`)으로 이미 로그인돼 있어야 한다 — 이 어댑터는 로그인을 대신 해주지
않는다.
"""

import asyncio
import json
import time
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import LLMExecutionError, LLMSchemaViolation

logger = structlog.get_logger()

_SESSION_TTL_SECONDS = 300  # Anthropic ephemeral 캐시 기본 TTL(5분)에 맞춘다
_MAX_TURNS_PER_SESSION = 12  # 세션이 무한정 자라 캐시 이득보다 비용이 커지는 걸 막는 상한
_SYSTEM_PROMPT = (
    "너는 이력서/지원 서류 문구 생성기다. 지시받은 형식으로만 답하고 새 사실을 지어내지 않는다."
)


class _Session:
    __slots__ = ("id", "last_used", "turns")

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.turns = 0
        self.last_used = time.monotonic()


class ClaudeCodeCliLLM:
    """`ports/llm.py`의 구현. `claude` CLI(로그인된 구독)를 headless 로 호출한다."""

    def __init__(
        self,
        *,
        binary: str = "claude",
        model: str = "claude-sonnet-5",
        max_budget_usd: float | None = 0.5,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._binary = binary
        self._model = model
        self._max_budget_usd = max_budget_usd
        self._timeout_seconds = timeout_seconds
        self._sessions: dict[str, _Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def complete(
        self, prompt: str, *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> str:
        envelope = await self._run(prompt, cache_key=cache_key)
        result = envelope.get("result")
        if not isinstance(result, str):
            raise LLMExecutionError(f"claude CLI 응답에 result 가 없다: {envelope}")
        return result

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_key: str | None = None
    ) -> T:
        envelope = await self._run(
            prompt, cache_key=cache_key, json_schema=schema.model_json_schema()
        )
        payload = envelope.get("structured_output")
        if payload is None:
            raise LLMSchemaViolation(f"{schema.__name__}: structured_output 이 없다: {envelope}")
        try:
            return schema.model_validate(payload)
        except ValidationError as e:
            raise LLMSchemaViolation(f"{schema.__name__}: {e}") from e

    async def _run(
        self,
        prompt: str,
        *,
        cache_key: str | None,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(cache_key, asyncio.Lock()) if cache_key else asyncio.Lock()
        async with lock:
            session = self._session_for(cache_key) if cache_key else None
            args = self._build_args(prompt, json_schema=json_schema, session=session)

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout_seconds
                )
            except TimeoutError as e:
                proc.kill()
                await proc.wait()
                raise LLMExecutionError(f"claude CLI 타임아웃({self._timeout_seconds}s)") from e

            if proc.returncode != 0:
                raise LLMExecutionError(
                    f"claude CLI 종료 코드 {proc.returncode}: "
                    f"{stderr.decode(errors='replace')[:500]}"
                )
            try:
                envelope: dict[str, Any] = json.loads(stdout)
            except json.JSONDecodeError as e:
                raise LLMExecutionError(
                    f"claude CLI 출력이 JSON 이 아니다: {stdout[:500]!r}"
                ) from e

            if envelope.get("is_error"):
                raise LLMExecutionError(f"claude CLI 에러 응답: {envelope}")

            logger.info(
                "claude_code_cli_call",
                cache_key=cache_key,
                session_reused=bool(session and session.turns > 0),
                cost_usd=envelope.get("total_cost_usd"),
                cache_read_tokens=envelope.get("usage", {}).get("cache_read_input_tokens"),
                cache_creation_tokens=envelope.get("usage", {}).get("cache_creation_input_tokens"),
            )

            if session is not None:
                session.turns += 1
                session.last_used = time.monotonic()
            return envelope

    def _build_args(
        self,
        prompt: str,
        *,
        json_schema: dict[str, Any] | None,
        session: "_Session | None",
    ) -> list[str]:
        args = [
            self._binary,
            "-p",
            prompt,
            "--model",
            self._model,
            "--system-prompt",
            _SYSTEM_PROMPT,
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "json",
        ]
        if self._max_budget_usd is not None:
            args += ["--max-budget-usd", str(self._max_budget_usd)]
        if json_schema is not None:
            args += ["--json-schema", json.dumps(json_schema)]

        if session is None:
            # cache_key 없이 부른 1회성 호출 — 디스크에 세션을 남기지 않는다.
            args += ["--no-session-persistence"]
        elif session.turns == 0:
            args += ["--session-id", session.id]
        else:
            args += ["--resume", session.id]
        return args

    def _session_for(self, cache_key: str) -> _Session:
        existing = self._sessions.get(cache_key)
        now = time.monotonic()
        if (
            existing is not None
            and existing.turns < _MAX_TURNS_PER_SESSION
            and now - existing.last_used < _SESSION_TTL_SECONDS
        ):
            return existing
        session = _Session(str(uuid.uuid4()))
        self._sessions[cache_key] = session
        return session
