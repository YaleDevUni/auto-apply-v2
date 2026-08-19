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

캐시(§ 사용자 요청 "cache 적극 활용", 재설계는 [[claude-cli-prompt-cache-redesign]]):
처음엔 `--resume`으로 세션을 이어야만 캐시가 붙는다고 실측했지만, 그건 "프롬프트 앞부분만
같고 뒷부분이 다른" 상황에서 세션 없이는 캐시가 하나도 안 붙는다는 것만 확인한 결과였다.
다시 실측해보니 진짜 원인은 세션 유무가 아니라 **콘텐츠 블록을 안 나눈 것**이었다 — `-p
<문자열>` 로 프롬프트 전체를 하나의 블록으로 보내면, 앞부분이 바이트 단위로 완전히 같아도
뒷부분이 한 글자만 달라지면 그 블록 전체가 캐시 미스로 처리된다(부분 프리픽스 매칭이 전혀
안 됨 — 실측: 27482 토큰짜리 공통 접두어에 뒤쪽 20자만 다른 두 번의 세션 없는 호출이 각각
독립적으로 `cache_creation`만 찍고 `cache_read`는 0이었다). 반대로 `--input-format
stream-json` 으로 안정적인 부분과 매번 바뀌는 부분을 **별도 텍스트 블록**으로 나누고
안정적인 블록에만 `cache_control`을 찍으면, 세션을 전혀 안 이어도(`--no-session-persistence`
그대로 둔 채) 완전히 독립된 두 번째 프로세스가 그 블록을 `cache_read`로 읽었다(실측:
27478 토큰 `cache_creation` → 다음 호출 `cache_read_input_tokens=27420` + 새 접미어만
`cache_creation=52`). 그래서 세션 재사용(`--session-id`/`--resume`) 기반 캐싱은 걷어내고,
호출자가 `cache_prefix`(안정적인 부분)와 `prompt`(매번 바뀌는 부분)를 나눠 넘기면 그 경계에
`cache_control`을 찍는 방식으로 바꿨다 — 세션 상태를 안 들고 있어도 되니 프로세스 간 락도
필요 없어졌고, 세션 재사용이 갖고 있던 오염 위험(이전 공고의 대화가 다음 공고 생성에 섞여
들어가는 것)도 구조적으로 사라졌다.

전제: 이 프로세스를 실행하는 머신에 `claude` CLI 가 설치되고 `claude login`(또는
`claude setup-token`)으로 이미 로그인돼 있어야 한다 — 이 어댑터는 로그인을 대신 해주지
않는다.
"""

import asyncio
import json
import re
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import (
    LLMAuthRequired,
    LLMExecutionError,
    LLMQuotaExceeded,
    LLMSchemaViolation,
)

logger = structlog.get_logger()

_SYSTEM_PROMPT = (
    "너는 이력서/지원 서류 문구 생성기다. 지시받은 형식으로만 답하고 새 사실을 지어내지 않는다."
)

# 실측 시그니처(2026-08-19, claude 2.1.235) — CLAUDE_CONFIG_DIR 를 빈 디렉터리로 돌려
# "로그아웃" 상태를 흉내내고, --max-budget-usd 를 극단적으로 낮춰 "한도초과"를 흉내냈다.
# 로그아웃: {"is_error":true,"result":"Not logged in · Please run /login",
#           "api_error_status":null, ...}
# 한도초과: {"is_error":true,"terminal_reason":"budget_exhausted",
#           "subtype":"error_max_budget_usd", ...}
# 두 경우 다 exit code 는 1 이지만 stdout 은 유효한 JSON 이라, JSON 파싱을 exit code 체크보다
# 먼저 시도해야 이 필드들을 볼 수 있다(구 코드는 exit code 부터 봐서 이 정보를 버렸다).
_AUTH_PATTERN = re.compile(
    r"not logged in|please run /login|oauth token (expired|revoked)"
    r"|invalid api key|authentication failed",
    re.IGNORECASE,
)
_QUOTA_PATTERN = re.compile(r"usage limit reached|credit balance.*too low", re.IGNORECASE)
_QUOTA_TERMINAL_REASONS = {"budget_exhausted"}
_QUOTA_SUBTYPES = {"error_max_budget_usd"}


def _classify_error(envelope: dict[str, Any]) -> LLMExecutionError:
    """`is_error` 응답을 CLI 의 실패 시그니처로 분류한다.

    패턴은 claude CLI 바이너리 안의 auth-실패 감지 정규식(전략: "usage limit reached" 등)을
    실측해서 그대로 옮겼다 — CLI 가 스스로 "이건 로그인/과금 문제다"라고 구분하는 문자열이라
    우리가 따로 정의하는 것보다 CLI 버전이 바뀌어도 어긋날 확률이 낮다.
    """
    result_text = str(envelope.get("result") or "")
    api_status = envelope.get("api_error_status")
    if _AUTH_PATTERN.search(result_text) or api_status == 401:
        return LLMAuthRequired(f"claude CLI 로그인 필요: {result_text or envelope}")
    if (
        _QUOTA_PATTERN.search(result_text)
        or envelope.get("terminal_reason") in _QUOTA_TERMINAL_REASONS
        or envelope.get("subtype") in _QUOTA_SUBTYPES
        or api_status == 429
    ):
        return LLMQuotaExceeded(f"claude CLI 사용량 한도 초과: {result_text or envelope}")
    return LLMExecutionError(f"claude CLI 에러 응답: {envelope}")


def _build_stdin_payload(prompt: str, cache_prefix: str) -> bytes:
    """`--input-format stream-json` 이 기대하는 한 줄짜리 사용자 메시지를 만든다.

    `cache_prefix`(안정적인 부분)가 있으면 별도 블록으로 떼어 그 블록에만 `cache_control`을
    찍는다 — 매번 바뀌는 `prompt`와 같은 블록에 이어붙이면 부분 매칭이 안 된다(모듈 docstring
    참고). `prompt`가 빈 문자열인데 `cache_prefix`만 있는 경우(재프롬프트 루프의 1번째 시도)는
    빈 텍스트 블록을 추가하지 않는다.
    """
    content: list[dict[str, Any]] = []
    if cache_prefix:
        content.append(
            {
                "type": "text",
                "text": cache_prefix,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        )
    if prompt or not content:
        content.append({"type": "text", "text": prompt})
    envelope = {"type": "user", "message": {"role": "user", "content": content}}
    return (json.dumps(envelope) + "\n").encode()


def _find_result_line(stdout: bytes) -> dict[str, Any] | None:
    """`--output-format stream-json` 출력(JSONL)에서 마지막 `"type":"result"` 요약 줄을 찾는다.

    성공/실패 모두 이 줄 하나로 수렴한다 — 나머지 줄(assistant/tool_use 등)은 무시한다.
    """
    last: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            last = obj
    return last


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

    async def complete(self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = "") -> str:
        envelope = await self._run(prompt, cache_prefix=cache_prefix)
        result = envelope.get("result")
        if not isinstance(result, str):
            raise LLMExecutionError(f"claude CLI 응답에 result 가 없다: {envelope}")
        return result

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T:
        envelope = await self._run(
            prompt, cache_prefix=cache_prefix, json_schema=schema.model_json_schema()
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
        cache_prefix: str,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = self._build_args(json_schema=json_schema)
        stdin_payload = _build_stdin_payload(prompt, cache_prefix)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_payload), timeout=self._timeout_seconds
            )
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise LLMExecutionError(f"claude CLI 타임아웃({self._timeout_seconds}s)") from e

        envelope = _find_result_line(stdout)
        if envelope is None:
            if proc.returncode != 0:
                raise LLMExecutionError(
                    f"claude CLI 종료 코드 {proc.returncode}: "
                    f"{stderr.decode(errors='replace')[:500]}"
                )
            raise LLMExecutionError(f"claude CLI 출력에 결과 라인이 없다: {stdout[:500]!r}")

        if envelope.get("is_error"):
            raise _classify_error(envelope)
        if proc.returncode != 0:
            raise LLMExecutionError(
                f"claude CLI 종료 코드 {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )

        usage = envelope.get("usage", {})
        logger.info(
            "claude_code_cli_call",
            cache_prefix_len=len(cache_prefix),
            cost_usd=envelope.get("total_cost_usd"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        )
        return envelope

    def _build_args(self, *, json_schema: dict[str, Any] | None) -> list[str]:
        args = [
            self._binary,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
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
            "--no-session-persistence",
        ]
        if self._max_budget_usd is not None:
            args += ["--max-budget-usd", str(self._max_budget_usd)]
        if json_schema is not None:
            args += ["--json-schema", json.dumps(json_schema)]
        return args
