"""`RecipeExecutor` port 의 3번째 구현 (메모리 agent-browser-executor-design).

PlaywrightExecutor 와 완전히 같은 계약을 지킨다 — 그래서 `test_executor_contract.py`가
셋 다에 돈다. Playwright 를 대체하지 않는다: `EXECUTOR=agent_browser` 로 선택하는 별도
엔진이다. 존재 이유는 CDP accessibility tree 해석 차이 — `recipe-builder`가 agent-browser
로 라이브 탐색해 selector 를 확정해도, Playwright 자체 accessible-name 계산이 달라서 실행
단계에서 다르게 동작하는 사례를 wanted 지원 폼에서 실측했다(2026-08-20). 디버깅 엔진과
실행 엔진을 agent-browser 로 통일하면 이 "번역 계층 버그"가 원천적으로 없어진다.

agent-browser CLI 는 매 명령마다 새 subprocess 를 띄우지만(`--session <name>` 으로 같은
브라우저 데몬에 묶인다), 브라우저 자체는 세션 이름 기준으로 데몬에 유지된다 — Playwright 처럼
프로세스 안에서 살아있는 Page 객체를 들고 있지 않고, 매 액션마다 CLI 를 새로 부른다.

**selector 지원 범위** (`domain/agent_browser_selector.classify_selector`) — agent-browser
는 브라우저 네이티브 `document.querySelector`로 raw CSS 를 해석해서, Playwright 가 CSS
위에 얹은 확장 문법을 그대로 못 받는다:
  - plain CSS → `click`/`fill`/`select`/`upload`/`wait`/`is visible` 명령에 그대로 넘긴다.
  - Playwright 엔진-프리픽스(`text=`, `role=[name=]`, `label=`, ...) → `find` 서브커맨드로
    번역한다. `find` 가 지원하는 액션이 click/fill/type/hover/focus/check/uncheck 뿐이라
    (`select`/`wait`/`assert_visible` 는 지원 안 함), 이 selector 형태는 CLICK/SUBMIT/FILL
    에서만 쓸 수 있다 — 단 `text=` 는 WAIT_FOR 에서 `wait --text`로도 매핑된다(agent-browser
    가 텍스트 등장 대기를 네이티브로 지원해서).
  - `:has-text()`/`:text-is()` (agent-browser 엔진에 없는 CSS 확장) → JS `eval` 로 직접
    찾아서 액션을 실행한다. CLICK/FILL 은 한 eval 호출로 찾기+실행을 같이 하고,
    WAIT_FOR/ASSERT_VISIBLE 은 조건(존재/보임)을 폴링한다(`_poll_until`, agent-browser 에
    이 형태의 selector 를 받는 네이티브 wait/is 가 없어서). UPLOAD 는 지원하지 않는다
    (`<input type=file>` 값은 보안상 JS 로 못 채운다) — plain CSS 만 허용.
  - 위 셋 다 아니면 그대로 CSS 로 취급하고 실패하면 RecipeExecutionError 로 드러난다.
  이 지원 범위 밖의 selector 로 recipe 를 짜면(예: SELECT/UPLOAD 에 `:has-text()`) `ValueError`
  → `RecipeExecutionError` 로 즉시 실패한다. recipe-builder 가 EXECUTOR 설정을 보고 그
  문법에 맞는 selector 를 고르는 문제는 다음 세션으로 남겨뒀다(메모리 참고).

action.timeout_ms 는 agent-browser CLI 에 정확히 전달할 옵션이 없어서(`wait` 서브커맨드는
`--download` 모드에만 `--timeout` 이 있다, CLI 자체 기본은 25초) subprocess 레벨
`asyncio.wait_for` 여유시간(+10s)으로만 강제한다 — CLI 자체 타임아웃이 보통 먼저 터진다.
"""

import asyncio
import base64
import contextlib
import json
import tempfile
import time
from datetime import UTC
from pathlib import Path

from auto_apply.contracts.dto import ExecutionContext, ExecutionResult
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.agent_browser_selector import (
    FindLocator,
    ResolvedSelector,
    TextFilterTarget,
    classify_selector,
)
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import AuthRequired, CaptchaEncountered, RecipeExecutionError
from auto_apply.domain.recipe_selector import resolve_selector
from auto_apply.ports.clock import Clock
from auto_apply.ports.storage import BlobStore

# PlaywrightExecutor 와 같은 마커 목록(§9.5) — 독립 구현이라 각자 들고 있는다.
_CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "cf-turnstile", "자동입력 방지", "보안문자")
_POLL_INTERVAL_S = 0.25


class _CliFailure(Exception):
    """agent-browser CLI 가 실패(exit != 0, success:false, subprocess 타임아웃)했을 때만 쓴다."""


class AgentBrowserExecutor:
    def __init__(
        self,
        clock: Clock,
        store: BlobStore,
        *,
        auth_dir: Path,
        binary: str = "agent-browser",
        headless: bool = True,
    ) -> None:
        self._clock = clock
        self._store = store
        self._auth_dir = auth_dir
        self._binary = binary
        self._headless = headless

    async def run(
        self, recipe: AutomationRecipe, ctx: ExecutionContext, mode: ExecutionMode
    ) -> ExecutionResult:
        state_path = self._auth_dir / f"{recipe.platform}.json"
        if not state_path.is_file():
            raise AuthRequired(
                f"{recipe.platform} 로그인 상태가 없다 — "
                f"scripts/save_auth_state.py 로 먼저 로그인해라 ({state_path})"
            )
        # platform/company 당 동시성 1 정책(§ 메모리 ats-web-agent-executor-design)을 그대로
        # 따른다는 전제로 attempt 까지 넣어 세션명을 고정 — 같은 이름이 재사용되면 이전 실행의
        # 열린 탭이 남아 헷갈릴 수 있어서다.
        session = f"aa-{ctx.application_id}-{ctx.attempt}"
        try:
            return await self._run_actions(recipe, ctx, mode, session, state_path)
        finally:
            await self._close(session)

    async def _run_actions(
        self,
        recipe: AutomationRecipe,
        ctx: ExecutionContext,
        mode: ExecutionMode,
        session: str,
        state_path: Path,
    ) -> ExecutionResult:
        artifacts: list[str] = []
        state_applied = False
        for i, action in enumerate(recipe.actions):
            if action.type is ActionType.SUBMIT:
                if mode is ExecutionMode.DRY_RUN:
                    return ExecutionResult(
                        outcome=AttemptOutcome.SUCCEEDED,
                        submitted_at=None,
                        artifact_keys=artifacts,
                        detail="dry_run: submit 을 실행하지 않았다",
                    )
                await self._check_captcha(session, recipe, ctx)

            apply_state = None
            if action.type is ActionType.GOTO and not state_applied:
                apply_state = state_path
                state_applied = True
            key = await self._run_one(i, action, ctx, recipe, session, state_path=apply_state)
            if key:
                artifacts.append(key)
            if action.type is ActionType.GOTO:
                await self._check_captcha(session, recipe, ctx)

        submitted = self._clock.now().astimezone(UTC) if recipe.has_submit else None
        return ExecutionResult(
            outcome=AttemptOutcome.SUCCEEDED,
            submitted_at=submitted,
            artifact_keys=artifacts,
            detail=f"agent_browser/{mode}",
        )

    async def _run_one(
        self,
        index: int,
        action: Action,
        ctx: ExecutionContext,
        recipe: AutomationRecipe,
        session: str,
        *,
        state_path: Path | None,
    ) -> str | None:
        try:
            return await self._dispatch(index, action, ctx, session, state_path=state_path)
        except (_CliFailure, ValueError) as e:
            # ValueError = value_ref/selector 치환에 쓸 값이 없거나(resolve_selector), 이 실행기가
            # 지원하지 않는 selector 형태다(classify_selector 결과가 액션과 안 맞음).
            # PlaywrightExecutor 와 같은 계약: optional 이면 이 스텝만 건너뛴다.
            if action.optional:
                return None
            snapshot_key = await self._snapshot(session, recipe, ctx, index)
            raise RecipeExecutionError(
                f"{action.type} 실패 ({action.selector}): {e}",
                snapshot_key=snapshot_key,
                form_hash=recipe.form_hash,
            ) from e

    async def _dispatch(
        self,
        index: int,
        action: Action,
        ctx: ExecutionContext,
        session: str,
        *,
        state_path: Path | None,
    ) -> str | None:
        target: ResolvedSelector | None = None
        if action.selector:
            target = classify_selector(resolve_selector(action, ctx.profile))
        timeout_s = self._timeout_s(action)

        match action.type:
            case ActionType.GOTO:
                url = self._resolve_value(action, ctx)
                await self._cli(session, ["open", url], timeout_s=timeout_s, state_path=state_path)
            case ActionType.CLICK | ActionType.SUBMIT:
                assert target is not None
                await self._act(session, target, "click", timeout_s=timeout_s)
            case ActionType.FILL:
                assert target is not None
                value = self._resolve_value(action, ctx)
                await self._act(session, target, "fill", value=value, timeout_s=timeout_s)
            case ActionType.SELECT:
                assert target is not None
                if not isinstance(target, str):
                    raise ValueError(
                        f"agent_browser 는 select 에 CSS selector 만 지원한다: {action.selector}"
                    )
                value = self._resolve_value(action, ctx)
                await self._cli(session, ["select", target, value], timeout_s=timeout_s)
            case ActionType.UPLOAD:
                assert target is not None
                if not isinstance(target, str):
                    raise ValueError(
                        f"agent_browser 는 upload 에 CSS selector 만 지원한다: {action.selector}"
                    )
                await self._dispatch_upload(session, target, action, ctx, timeout_s)
                return None
            case ActionType.WAIT_FOR:
                assert target is not None
                await self._wait(session, target, action)
            case ActionType.ASSERT_VISIBLE:
                assert target is not None
                await self._assert_visible(session, target, action)
            case ActionType.SCREENSHOT:
                return await self._dispatch_screenshot(session, index, ctx)
        return None

    # ── selector 종류별 실행 ─────────────────────────────────────────────
    async def _act(
        self,
        session: str,
        target: ResolvedSelector,
        cli_action: str,
        *,
        value: str | None = None,
        timeout_s: float,
    ) -> None:
        if isinstance(target, str):
            args = [cli_action, target, *([value] if value is not None else [])]
            await self._cli(session, args, timeout_s=timeout_s)
            return
        if isinstance(target, FindLocator):
            if cli_action not in ("click", "fill"):
                raise ValueError(f"agent_browser find 매핑은 click/fill 만 지원한다: {cli_action}")
            args = ["find", target.locator, target.value, cli_action]
            if value is not None:
                args.append(value)
            if target.locator == "role" and target.name:
                args += ["--name", target.name]
            if target.exact:
                args.append("--exact")
            await self._cli(session, args, timeout_s=timeout_s)
            return
        await self._act_via_eval(session, target, cli_action, value=value, timeout_s=timeout_s)

    async def _act_via_eval(
        self,
        session: str,
        target: TextFilterTarget,
        cli_action: str,
        *,
        value: str | None,
        timeout_s: float,
    ) -> None:
        find_expr = target.to_js_finder()
        if cli_action == "click":
            body = (
                "(() => { "
                f"const el = {find_expr}; if (!el) throw new Error('NOT_FOUND'); "
                "el.click(); return 'OK'; })()"
            )
        elif cli_action == "fill":
            js_value = json.dumps(value)
            body = (
                "(() => { "
                f"const el = {find_expr}; if (!el) throw new Error('NOT_FOUND'); "
                "const proto = Object.getPrototypeOf(el); "
                "const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set; "
                f"if (setter) setter.call(el, {js_value}); else el.value = {js_value}; "
                "el.dispatchEvent(new Event('input', {bubbles:true})); "
                "el.dispatchEvent(new Event('change', {bubbles:true})); "
                "return 'OK'; })()"
            )
        else:
            raise ValueError(
                f"agent_browser 텍스트-필터 selector 는 click/fill 만 지원한다: {cli_action}"
            )
        await self._eval(session, body, timeout_s=timeout_s)

    async def _wait(self, session: str, target: ResolvedSelector, action: Action) -> None:
        timeout_s = self._timeout_s(action)
        if isinstance(target, str):
            await self._cli(session, ["wait", target], timeout_s=timeout_s)
            return
        if isinstance(target, FindLocator) and target.locator == "text":
            await self._cli(session, ["wait", "--text", target.value], timeout_s=timeout_s)
            return
        if isinstance(target, TextFilterTarget):
            await self._poll_until(
                session, f"(({target.to_js_finder()}) !== null)", action.timeout_ms
            )
            return
        raise ValueError(
            f"agent_browser 는 이 selector 로 wait_for 를 지원하지 않는다: {action.selector}"
        )

    async def _assert_visible(self, session: str, target: ResolvedSelector, action: Action) -> None:
        if isinstance(target, str):
            data = await self._cli(
                session, ["is", "visible", target], timeout_s=self._timeout_s(action)
            )
            if not data.get("visible"):
                raise _CliFailure(f"visible 이 아니다: {target}")
            return
        if isinstance(target, TextFilterTarget):
            # agent-browser 는 `find` 액션에 "is visible" 이 없다(모듈 docstring) — 직접 폴링한다.
            visible_expr = (
                f"(() => {{ const el = {target.to_js_finder()}; if (!el) return false; "
                "if (el.getClientRects().length === 0) return false; "
                "return getComputedStyle(el).visibility !== 'hidden'; })()"
            )
            await self._poll_until(session, visible_expr, action.timeout_ms)
            return
        raise ValueError(
            "agent_browser 는 role=/text= selector 로 assert_visible 을 지원하지 않는다"
        )

    async def _poll_until(self, session: str, bool_expr: str, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            result = await self._eval(session, bool_expr, timeout_s=5.0)
            if result is True:
                return
            if time.monotonic() >= deadline:
                raise _CliFailure(f"시간 안에 조건을 만족하지 못했다({timeout_ms}ms)")
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def _dispatch_upload(
        self,
        session: str,
        css_selector: str,
        action: Action,
        ctx: ExecutionContext,
        timeout_s: float,
    ) -> None:
        filename, data = await self._resolve_upload(action, ctx)
        # 임시파일명을 blob key 마지막 경로 요소로 맞춘다(PlaywrightExecutor 와 같은 이유,
        # §3) — 플랫폼이 화면에 그 이름을 그대로 보여줘서 뒤이은 selector 매칭 기준이 된다.
        with tempfile.TemporaryDirectory(prefix="auto-apply-upload-") as tmp_dir:
            tmp_path = Path(tmp_dir) / filename
            tmp_path.write_bytes(data)
            await self._cli(session, ["upload", css_selector, str(tmp_path)], timeout_s=timeout_s)
        return None

    async def _dispatch_screenshot(self, session: str, index: int, ctx: ExecutionContext) -> str:
        with tempfile.TemporaryDirectory(prefix="auto-apply-screenshot-") as tmp_dir:
            tmp_path = Path(tmp_dir) / f"{index:02d}.png"
            await self._cli(session, ["screenshot", str(tmp_path)], timeout_s=15.0)
            data = tmp_path.read_bytes()
        key = f"application-artifacts/{ctx.application_id}/{ctx.attempt}/{index:02d}.png"
        await self._store.put(key, data, content_type="image/png")
        return key

    # ── 값 치환 (PlaywrightExecutor 와 같은 계약, §3) ────────────────────
    def _resolve_value(self, action: Action, ctx: ExecutionContext) -> str:
        if action.value_literal is not None:
            return action.value_literal
        assert action.value_ref is not None
        key = action.value_ref.removeprefix("profile.")
        if key not in ctx.profile:
            raise ValueError(f"프로필에 값이 없다: {action.value_ref}")
        return ctx.profile[key]

    async def _resolve_upload(self, action: Action, ctx: ExecutionContext) -> tuple[str, bytes]:
        assert action.value_ref is not None
        key = action.value_ref.removeprefix("upload.")
        if key not in ctx.upload_keys:
            raise ValueError(f"업로드 파일이 없다: {action.value_ref}")
        blob_key = ctx.upload_keys[key]
        data = await self._store.get(blob_key)
        return Path(blob_key).name, data

    def _timeout_s(self, action: Action) -> float:
        # agent-browser 에 action 단위 타임아웃을 정확히 넘길 옵션이 없다(모듈 docstring) —
        # subprocess 레벨 여유시간으로만 강제한다. CLI 자체 기본(25초)이 보통 먼저 터진다.
        return max(action.timeout_ms / 1000, 1.0) + 10.0

    # ── CAPTCHA / 스냅샷 ──────────────────────────────────────────────────
    async def _check_captcha(
        self, session: str, recipe: AutomationRecipe, ctx: ExecutionContext
    ) -> None:
        try:
            html = await self._eval(session, "document.documentElement.outerHTML", timeout_s=10.0)
        except _CliFailure:
            return
        content = (html or "").lower() if isinstance(html, str) else ""
        if any(marker.lower() in content for marker in _CAPTCHA_MARKERS):
            await self._snapshot(session, recipe, ctx, -1)
            raise CaptchaEncountered(f"{recipe.platform} 에서 CAPTCHA 감지")

    async def _snapshot(
        self, session: str, recipe: AutomationRecipe, ctx: ExecutionContext, index: int
    ) -> str:
        key = (
            f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/attempt-{ctx.attempt}-{index}.html"
        )
        try:
            html = await self._eval(session, "document.documentElement.outerHTML", timeout_s=10.0)
            if isinstance(html, str):
                await self._store.put(key, html.encode("utf-8"), content_type="text/html")
        except _CliFailure:
            pass
        return key

    # ── subprocess ────────────────────────────────────────────────────────
    async def _eval(self, session: str, script: str, *, timeout_s: float) -> object:
        b64 = base64.b64encode(script.encode()).decode()
        data = await self._cli(session, ["eval", "-b", b64], timeout_s=timeout_s)
        return data.get("result")

    async def _cli(
        self,
        session: str,
        args: list[str],
        *,
        timeout_s: float,
        state_path: Path | None = None,
    ) -> dict[str, object]:
        full = [self._binary, "--json", "--session", session]
        if not self._headless:
            full.append("--headed")
        if state_path is not None:
            full += ["--state", str(state_path)]
        full += args

        proc = await asyncio.create_subprocess_exec(
            *full, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise _CliFailure(f"agent-browser CLI 타임아웃({timeout_s}s): {' '.join(args)}") from e

        text = stdout.decode(errors="replace")
        try:
            payload = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            payload = None

        if proc.returncode != 0 or payload is None or not payload.get("success"):
            message = (
                (payload or {}).get("error") or stderr.decode(errors="replace")[:500] or text[:500]
            )
            raise _CliFailure(f"{' '.join(args[:3])} 실패: {message}")
        return payload.get("data") or {}

    async def _close(self, session: str) -> None:
        with contextlib.suppress(_CliFailure):
            await self._cli(session, ["close"], timeout_s=15.0)
