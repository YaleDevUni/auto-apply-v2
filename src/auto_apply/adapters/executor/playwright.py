"""RecipeExecutor port 의 실제 구현 (M2).

ReplayExecutor 와 완전히 같은 계약을 지킨다 — 그래서 contract test 하나가 둘 다에 돈다.
  - Recipe 가 현재 DOM 과 안 맞으면 RecipeExecutionError(snapshot_key, form_hash)
  - CAPTCHA 는 우회하지 않고 CaptchaEncountered 로 즉시 사람에게 넘긴다
  - 로그인 안 된 platform 은 AuthRequired — 비밀번호를 코드가 타이핑하지 않는다.
    storage_state 는 scripts/save_auth_state.py 로 사람이 직접 로그인해 만든다.
  - dry_run 은 submit 직전까지만 실행한다 (§2.4)
"""

import mimetypes
from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from auto_apply.adapters.executor._checkpoint import CheckpointWaiter
from auto_apply.contracts.dto import ExecutionContext, ExecutionResult
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import AuthRequired, CaptchaEncountered, RecipeExecutionError
from auto_apply.domain.recipe_selector import resolve_selector
from auto_apply.ports.clock import Clock
from auto_apply.ports.storage import BlobStore

# CDP 로 열리는 실제 Chromium 페이지 안에서 이 마커들이 보이면 사람에게 넘긴다 (§9.5).
_CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "cf-turnstile", "자동입력 방지", "보안문자")

_NEEDS_SELECTOR = {
    ActionType.CLICK,
    ActionType.FILL,
    ActionType.SELECT,
    ActionType.UPLOAD,
    ActionType.WAIT_FOR,
    ActionType.ASSERT_VISIBLE,
    ActionType.SUBMIT,
}


class PlaywrightExecutor:
    def __init__(
        self,
        clock: Clock,
        store: BlobStore,
        *,
        auth_dir: Path,
        headless: bool = True,
        checkpoint: CheckpointWaiter | None = None,
    ) -> None:
        self._clock = clock
        self._store = store
        self._auth_dir = auth_dir
        self._headless = headless
        self._checkpoint = checkpoint

    async def run(
        self,
        recipe: AutomationRecipe,
        ctx: ExecutionContext,
        mode: ExecutionMode,
        *,
        heartbeat: Callable[[str], None] | None = None,
    ) -> ExecutionResult:
        state_path = self._auth_dir / f"{recipe.platform}.json"
        if not state_path.is_file():
            raise AuthRequired(
                f"{recipe.platform} 로그인 상태가 없다 — "
                f"scripts/save_auth_state.py 로 먼저 로그인해라 ({state_path})"
            )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(storage_state=str(state_path))
                page = await context.new_page()
                try:
                    return await self._run_actions(recipe, ctx, mode, page, heartbeat)
                finally:
                    await context.close()
            finally:
                await browser.close()

    async def _run_actions(
        self,
        recipe: AutomationRecipe,
        ctx: ExecutionContext,
        mode: ExecutionMode,
        page: Page,
        heartbeat: Callable[[str], None] | None,
    ) -> ExecutionResult:
        artifacts: list[str] = []
        for i, action in enumerate(recipe.actions):
            if action.type is ActionType.SUBMIT:
                if mode is ExecutionMode.DRY_RUN:
                    # 누르지는 않지만 "대상이 실제로 있는지"는 확인하고 끝낸다. 이 확인이 없으면
                    # dry_run 이 submit selector 를 한 번도 평가하지 않아서, 깨진 submit selector 를
                    # 샌드박스 검증(=AutomationRepairWorkflow 의 dry-run)이 영원히 못 본다 —
                    # 수선 루프가 "고쳤다"고 판정한 뒤 live 에서 같은 자리에서 계속 실패한다
                    # (실측: wanted v1~v5 의 `button:text-is("제출하기")` 는 0개 매칭인데
                    # dry_run 은 5번 다 SUCCEEDED 였다). ReplayExecutor 는 원래 이렇게
                    # 동작했다 — 실행기 셋의 계약을 여기서 맞춘다.
                    await self._run_one(
                        i,
                        action.model_copy(update={"type": ActionType.ASSERT_VISIBLE}),
                        ctx,
                        recipe,
                        page,
                    )
                    return ExecutionResult(
                        outcome=AttemptOutcome.SUCCEEDED,
                        submitted_at=None,
                        artifact_keys=artifacts,
                        detail="dry_run: submit 대상만 확인하고 실행하지 않았다",
                    )
                await self._check_captcha(page, recipe, ctx)

            # SUBMIT 은 recipe 가 checkpoint 를 안 세워도 항상 막는다(CLAUDE.md 절대규칙 4).
            # DRY_RUN(샌드박스 포함)은 위에서 이미 return 했으므로 여기 닿지 않는다 — 자동으로
            # 안전하다.
            if (
                mode is ExecutionMode.SUPERVISED
                and self._checkpoint is not None
                and (action.checkpoint or action.type is ActionType.SUBMIT)
            ):
                screenshot = await page.screenshot()
                await self._checkpoint.wait(
                    application_id=ctx.application_id,
                    attempt=ctx.attempt,
                    label=f"{i:02d}:{action.type}",
                    screenshot=screenshot,
                    heartbeat=heartbeat,
                )

            key = await self._run_one(i, action, ctx, recipe, page)
            if key:
                artifacts.append(key)
            if action.type is ActionType.GOTO:
                await self._check_captcha(page, recipe, ctx)

        submitted = self._clock.now().astimezone(UTC) if recipe.has_submit else None
        return ExecutionResult(
            outcome=AttemptOutcome.SUCCEEDED,
            submitted_at=submitted,
            artifact_keys=artifacts,
            detail=f"playwright/{mode}",
        )

    async def _run_one(
        self,
        index: int,
        action: Action,
        ctx: ExecutionContext,
        recipe: AutomationRecipe,
        page: Page,
    ) -> str | None:
        try:
            return await self._dispatch(index, action, ctx, page)
        except (PlaywrightTimeoutError, PlaywrightError, ValueError) as e:
            # ValueError = value_ref/selector 치환에 쓸 값이 없거나 비어 있다(resolve_selector,
            # _resolve_value, _resolve_upload). Playwright 오류와 같은 급으로 취급한다 — optional
            # 이면 이 스텝만 건너뛴다(예: 카테고리 판정이 안 돼 포트폴리오 selector 에 꽂을 값이
            # 없는 경우).
            if action.optional:
                return None
            snapshot_key = await self._snapshot(page, recipe, ctx, index)
            raise RecipeExecutionError(
                f"{action.type} 실패 ({action.selector}): {e}",
                snapshot_key=snapshot_key,
                form_hash=recipe.form_hash,
            ) from e

    async def _dispatch(
        self, index: int, action: Action, ctx: ExecutionContext, page: Page
    ) -> str | None:
        locator: Locator | None = (
            page.locator(resolve_selector(action, ctx.profile)) if action.selector else None
        )

        match action.type:
            case ActionType.GOTO:
                await page.goto(self._resolve_value(action, ctx), timeout=action.timeout_ms)
            case ActionType.CLICK | ActionType.SUBMIT:
                assert locator is not None
                await locator.click(timeout=action.timeout_ms)
            case ActionType.FILL:
                assert locator is not None
                await locator.fill(self._resolve_value(action, ctx), timeout=action.timeout_ms)
            case ActionType.SELECT:
                assert locator is not None
                await locator.select_option(
                    self._resolve_value(action, ctx), timeout=action.timeout_ms
                )
            case ActionType.UPLOAD:
                assert locator is not None
                filename, data = await self._resolve_upload(action, ctx)
                mime_type, _ = mimetypes.guess_type(filename)
                await locator.set_input_files(
                    {
                        "name": filename,
                        "mimeType": mime_type or "application/octet-stream",
                        "buffer": data,
                    },
                    timeout=action.timeout_ms,
                )
            case ActionType.WAIT_FOR:
                assert locator is not None
                await locator.wait_for(timeout=action.timeout_ms)
            case ActionType.ASSERT_VISIBLE:
                assert locator is not None
                await locator.wait_for(state="visible", timeout=action.timeout_ms)
            case ActionType.SCREENSHOT:
                data = await page.screenshot(timeout=action.timeout_ms)
                key = f"application-artifacts/{ctx.application_id}/{ctx.attempt}/{index:02d}.png"
                await self._store.put(key, data, content_type="image/png")
                return key
        return None

    def _resolve_value(self, action: Action, ctx: ExecutionContext) -> str:
        if action.value_literal is not None:
            return action.value_literal
        assert action.value_ref is not None  # 스키마 model_validator 가 이미 보장한다
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
        # blob key 의 마지막 경로 요소를 그대로 업로드 파일명으로 쓴다 — 플랫폼이 화면에
        # 그 이름을 그대로 보여주므로(실측, wanted), selector 로 "방금 올린 파일" 을 다시
        # 찾을 때 이 이름이 매칭 기준이 된다. 원래 tempfile 로 랜덤/확장자 불일치 이름을
        # 넘기던 버그를 고친 것 — Playwright 는 path 대신 buffer+name 을 직접 받을 수 있다.
        return Path(blob_key).name, data

    async def _check_captcha(
        self, page: Page, recipe: AutomationRecipe, ctx: ExecutionContext
    ) -> None:
        content = (await page.content()).lower()
        if any(marker.lower() in content for marker in _CAPTCHA_MARKERS):
            await self._snapshot(page, recipe, ctx, -1)
            raise CaptchaEncountered(f"{recipe.platform} 에서 CAPTCHA 감지")

    async def _snapshot(
        self, page: Page, recipe: AutomationRecipe, ctx: ExecutionContext, index: int
    ) -> str:
        key = (
            f"dom-snapshots/{recipe.platform}/{recipe.form_hash}/attempt-{ctx.attempt}-{index}.html"
        )
        try:
            html = await page.content()
            await self._store.put(key, html.encode("utf-8"), content_type="text/html")
        except PlaywrightError:
            pass
        return key
