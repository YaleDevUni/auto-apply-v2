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

cache_control 4개 초과 폴백: 위 캐싱을 라이브 배치로 돌리다(2026-08-21) CLI 가 큰 프롬프트에서
"A maximum of 4 blocks with cache_control may be provided. Found 5." 400 을 내는 걸 재현했다
— CLI 가 이미 내부적으로 몇 개의 브레이크포인트를 쓰고 있어서 우리가 붙이는 것까지 합치면
한도를 넘는 것으로 보이는데, 어떤 프롬프트에서 재현되는지는 CLI 내부 구현에 달려 있어 우리
쪽에서 미리 피할 수 없다. 같은 activity 를 Temporal 이 재시도해도 프롬프트 크기가 그대로면
매번 다시 걸려 재시도 예산을 태울 뿐이라(`_AI_RETRY`, `workflows/resume.py`), `_run`이 이
시그니처(`_CACHE_OVERFLOW_PATTERN`)를 만나면 그 자리에서 `cache_control` 없이 한 번 더
호출한다 — 캐싱은 최적화일 뿐 정확성엔 필요 없다.

turn()(§ 대화형 에이전트 오버헤드 절감, 2026-08-24): 위 설계는 매 호출마다 새 프로세스를
띄운다 — 격리는 완벽하지만(콘텐츠 오염 불가능) 프로세스 기동 자체의 고정비용(실측 ~3.5초,
auto-memory·백그라운드 프리페치·keychain 읽기 등 `duration_ms`에 안 잡히는 부분 — `--bare`를
쓰면 없어지지만 그러면 OAuth 구독 대신 API 키 과금으로 강제 전환되니 못 쓴다)을 호출마다
다시 낸다. 대화형 에이전트의 ReAct 루프처럼 한 번의
사용자 메시지 안에서 여러 번 구조화 호출을 반복하는 곳은 이 비용을 여러 번 내는 게 낭비다.
실측(2026-08-24): 프로세스를 살려둔 채 stdin 으로 메시지를 또 보내면 재기동 비용 없이
순수 모델 응답 시간만 든다 — 근데 대화 맥락이 자동으로 이어진다(직전 턴 내용을 정확히
기억함). `/clear` 를 보내면 새 session_id 로 전환되고 실제로 이전 내용을 잊는다(LLM 호출
없이 30ms, 사실상 공짜) — 프로세스 재사용 + `/clear` 조합이면 세션 오염 없이 기동 비용만
아낄 수 있다. 문제는 `/clear`가 슬래시커맨드라 `--disable-slash-commands`가 있으면 막힌다
("/clear isn't available in this environment.") — 그렇다고 이 플래그를 빼면 `system init`
이벤트에 스킬/슬래시커맨드 44개 전체가 노출된다(`--tools ""`/`--strict-mcp-config`가
여전히 막아줘서 실제로 실행은 안 되지만 표면은 넓어진다). 외부 공고 텍스트가 프롬프트에
들어가는 이력서 생성 쪽엔 이 표면을 열 이유가 없어서, `allow_slash_commands=True`로 명시
설정한 인스턴스에서만 `turn()`을 허용한다 — 이력서 생성용 인스턴스는 기본값(False)이다.
실제 프로세스 스트리밍 구현은 `_claude_cli_turn.py`(companion 모듈, 파일 크기 분리)에 있다.
"""

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, ValidationError

from auto_apply.domain.errors import (
    LLMAuthRequired,
    LLMExecutionError,
    LLMQuotaExceeded,
    LLMSchemaViolation,
)

if TYPE_CHECKING:
    # 런타임 import 는 turn() 안에서 지연으로 한다 — _claude_cli_turn.py 가 이 파일의
    # _build_stdin_payload/_classify_error/logger 를 최상단에서 가져다 쓰기 때문에, 이
    # 파일이 최상단에서 그쪽을 다시 가져오면 순환 import 가 된다. 타입 체크용으로만 쓴다.
    from auto_apply.adapters.llm._claude_cli_turn import ClaudeCliTurn

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

# 실측(2026-08-21, 라이브 배치 실행 중 재현): CLI 가 큰 프롬프트에서 자체적으로 이미 몇 개의
# cache_control 브레이크포인트를 쓰고 있어서, 우리가 cache_prefix 에 붙이는 것까지 합치면
# Anthropic API 의 "요청당 최대 4개" 한도를 넘는 경우가 있다("A maximum of 4 blocks with
# cache_control may be provided. Found 5." 400 응답). 재현 조건이 불명확하고(같은 크기의
# 다른 요청은 통과) CLI 내부 구현에 달려 있어 우리 쪽에서 예측/회피할 수 없다 — 캐싱은
# 최적화일 뿐 정확성에 필요하지 않으므로, 이 패턴이 뜨면 cache_control 없이 한 번 더 시도해
# 정확성을 지킨다(`_run`).
_CACHE_OVERFLOW_PATTERN = re.compile(r"maximum of \d+ blocks with cache_control", re.IGNORECASE)


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


def _build_stdin_payload(
    prompt: str, cache_prefix: str, *, use_cache_control: bool = True
) -> bytes:
    """`--input-format stream-json` 이 기대하는 한 줄짜리 사용자 메시지를 만든다.

    `cache_prefix`(안정적인 부분)가 있으면 별도 블록으로 떼어 그 블록에만 `cache_control`을
    찍는다 — 매번 바뀌는 `prompt`와 같은 블록에 이어붙이면 부분 매칭이 안 된다(모듈 docstring
    참고). `prompt`가 빈 문자열인데 `cache_prefix`만 있는 경우(재프롬프트 루프의 1번째 시도)는
    빈 텍스트 블록을 추가하지 않는다. `use_cache_control=False`는 `_CACHE_OVERFLOW_PATTERN`
    재시도 전용 — 블록은 그대로 나누되 `cache_control`만 뺀다.
    """
    content: list[dict[str, Any]] = []
    if cache_prefix:
        block: dict[str, Any] = {"type": "text", "text": cache_prefix}
        if use_cache_control:
            block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        content.append(block)
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
        allow_slash_commands: bool = False,
    ) -> None:
        self._binary = binary
        self._model = model
        self._max_budget_usd = max_budget_usd
        self._timeout_seconds = timeout_seconds
        # turn()(모듈 docstring 참고) 전용 스위치 — 기본은 잠금. 켜면 매 호출(1회성 호출
        # 포함)에서 --disable-slash-commands 가 빠져 슬래시커맨드 44개가 모델에 노출된다.
        # 외부 텍스트가 프롬프트에 들어가는 인스턴스에선 절대 켜면 안 된다.
        self._allow_slash_commands = allow_slash_commands

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
        envelope = await self._invoke(
            prompt, cache_prefix=cache_prefix, json_schema=json_schema, use_cache_control=True
        )
        if envelope.get("is_error") and _CACHE_OVERFLOW_PATTERN.search(
            str(envelope.get("result") or "")
        ):
            logger.warning(
                "claude_code_cli_cache_overflow_fallback", cache_prefix_len=len(cache_prefix)
            )
            envelope = await self._invoke(
                prompt, cache_prefix=cache_prefix, json_schema=json_schema, use_cache_control=False
            )

        if envelope.get("is_error"):
            raise _classify_error(envelope)

        usage = envelope.get("usage", {})
        logger.info(
            "claude_code_cli_call",
            cache_prefix_len=len(cache_prefix),
            cost_usd=envelope.get("total_cost_usd"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        )
        return envelope

    async def _invoke(
        self,
        prompt: str,
        *,
        cache_prefix: str,
        json_schema: dict[str, Any] | None,
        use_cache_control: bool,
    ) -> dict[str, Any]:
        args = self._build_args(json_schema=json_schema)
        stdin_payload = _build_stdin_payload(
            prompt, cache_prefix, use_cache_control=use_cache_control
        )

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

        if not envelope.get("is_error") and proc.returncode != 0:
            raise LLMExecutionError(
                f"claude CLI 종료 코드 {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
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
        ]
        if not self._allow_slash_commands:
            args.append("--disable-slash-commands")
        args += [
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

    def turn(self) -> "ClaudeCliTurn":
        """모듈 docstring "turn()" 절 참고. `allow_slash_commands=False`(기본값) 인스턴스는
        `/clear`가 막혀 있어 프로세스를 재사용하면 턴 사이 대화가 안 지워진 채 섞인다 —
        그래서 명시적으로 거부한다(조용히 격리를 포기하지 않는다)."""
        if not self._allow_slash_commands:
            raise RuntimeError(
                "turn() 은 allow_slash_commands=True 인스턴스에서만 쓸 수 있다 — 그렇지 않으면"
                " /clear 가 막혀서 프로세스 재사용 시 턴 사이 대화가 안 지워진 채 섞인다"
                "(claude_code_cli.py 모듈 docstring 'turn()' 절 참고)."
            )
        from auto_apply.adapters.llm._claude_cli_turn import ClaudeCliTurn

        return ClaudeCliTurn(self)

    async def _spawn(self, json_schema: dict[str, Any] | None) -> asyncio.subprocess.Process:
        """`ClaudeCliTurn` 전용 — 프로세스만 띄우고 반환한다(입출력 스트리밍은 호출자 담당)."""
        args = self._build_args(json_schema=json_schema)
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
