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
