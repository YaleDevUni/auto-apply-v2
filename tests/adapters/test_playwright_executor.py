"""PlaywrightExecutor 만의 동작 — 공유 계약은 tests/ports/test_executor_contract.py 가 커버한다.

여기서는 UPLOAD 액션이 실제 파일명을 브라우저에 넘기는지만 확인한다 — 예전엔
tempfile.NamedTemporaryFile(suffix=".upload") 를 그대로 넘겨서 플랫폼 화면에 랜덤
이름(게다가 확장자도 안 맞는)이 찍혔다(실측, wanted). Playwright 에 path 대신
name/mimeType/buffer 를 직접 넘기는 방식으로 고쳤다.
"""

from pathlib import Path

import pytest

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.executor.playwright import PlaywrightExecutor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import ExecutionContext
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import RecipeExecutionError

pytestmark = pytest.mark.integration

_UPLOAD_FORM_HTML = """
<!doctype html><html><body>
<input type="file" id="f"/>
<div id="uploaded-name"></div>
<script>
document.getElementById('f').addEventListener('change', (e) => {
  const file = e.target.files[0];
  document.getElementById('uploaded-name').textContent = file ? file.name : '';
});
</script>
</body></html>
"""


async def test_upload_preserves_the_blob_keys_filename(tmp_path: Path) -> None:
    form_path = tmp_path / "upload.html"
    form_path.write_text(_UPLOAD_FORM_HTML)
    form_url = form_path.as_uri()

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "fixture.json").write_text('{"cookies": [], "origins": []}')

    store = InMemoryBlobStore()
    await store.put("resumes/abc123.pdf", b"%PDF-1.4 fake", content_type="application/pdf")

    recipe = AutomationRecipe(
        platform="fixture",
        version=1,
        status="active",
        form_hash="h-upload-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal=form_url),
            Action(type=ActionType.UPLOAD, selector="#f", value_ref="upload.resume"),
            Action(
                type=ActionType.ASSERT_VISIBLE,
                selector='#uploaded-name:has-text("abc123.pdf")',
                timeout_ms=2_000,
            ),
        ],
        success_signals=["ok"],
    )
    ctx = ExecutionContext(
        application_id="app_1",
        attempt=1,
        upload_keys={"resume": "resumes/abc123.pdf"},
    )
    executor = PlaywrightExecutor(SystemClock(), store, auth_dir=auth_dir, headless=True)

    result = await executor.run(recipe, ctx, ExecutionMode.DRY_RUN)

    assert result.outcome is AttemptOutcome.SUCCEEDED


_SUBMIT_FORM_HTML = """
<!doctype html><html><body>
<button id="go"><span><span>제출하기</span></span></button>
<div id="clicked"></div>
<script>
document.getElementById('go').addEventListener('click', () => {
  document.getElementById('clicked').textContent = 'clicked';
});
</script>
</body></html>
"""


def _submit_recipe(form_url: str, submit_selector: str) -> AutomationRecipe:
    return AutomationRecipe(
        platform="fixture",
        version=1,
        status="active",
        form_hash="h-submit-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal=form_url),
            Action(type=ActionType.SUBMIT, selector=submit_selector, timeout_ms=2_000),
        ],
        success_signals=["ok"],
    )


def _submit_env(tmp_path: Path) -> tuple[str, PlaywrightExecutor]:
    form_path = tmp_path / "submit.html"
    form_path.write_text(_SUBMIT_FORM_HTML)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "fixture.json").write_text('{"cookies": [], "origins": []}')
    executor = PlaywrightExecutor(
        SystemClock(), InMemoryBlobStore(), auth_dir=auth_dir, headless=True
    )
    return form_path.as_uri(), executor


async def test_dry_run_fails_when_the_submit_selector_matches_nothing(tmp_path: Path) -> None:
    """dry_run 은 submit 을 누르지 않지만, 대상이 없다는 건 여기서 잡아야 한다.

    `button:text-is("제출하기")` 는 0개 매칭이다 — Playwright 텍스트 엔진은 그 텍스트를 가진
    가장 작은 요소(안쪽 span)만 매칭하기 때문. wanted recipe v1~v5 가 실제로 이 selector 였고,
    dry_run 이 submit 직전에 그냥 SUCCEEDED 로 끝나버려서 AutomationRepairWorkflow 의 샌드박스
    검증이 다섯 번 내리 "고쳤다"고 오판했다.
    """
    form_url, executor = _submit_env(tmp_path)
    recipe = _submit_recipe(form_url, 'button:text-is("제출하기")')
    ctx = ExecutionContext(application_id="app_submit_miss", attempt=1)

    with pytest.raises(RecipeExecutionError):
        await executor.run(recipe, ctx, ExecutionMode.DRY_RUN)


async def test_dry_run_verifies_the_submit_target_without_clicking(tmp_path: Path) -> None:
    form_url, executor = _submit_env(tmp_path)
    recipe = _submit_recipe(form_url, 'button:has-text("제출하기")')
    ctx = ExecutionContext(application_id="app_submit_ok", attempt=1)

    result = await executor.run(recipe, ctx, ExecutionMode.DRY_RUN)

    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is None
