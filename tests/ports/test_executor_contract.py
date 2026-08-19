"""RecipeExecutor contract test (ARCHITECTURE.md §11.2).

ports/executor.py 의 docstring 이 계약이다:
  - Recipe 가 현재 DOM 과 맞지 않으면 RecipeExecutionError
  - CAPTCHA 를 만나면 CaptchaEncountered
  - 로그인 안 된 platform 은 AuthRequired
  - dry_run 은 submit 직전까지만 실행한다

ReplayExecutor 는 진짜 브라우저 없이 이 계약을 재현하는 대역이므로, 같은 시나리오를
PlaywrightExecutor 에도 그대로 돌려서 두 구현이 같은 계약을 지키는지 확인한다.
브라우저를 띄우므로 전체를 integration 으로 표시한다.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.executor.playwright import PlaywrightExecutor
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import ExecutionContext
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import AuthRequired, CaptchaEncountered, RecipeExecutionError
from auto_apply.ports.executor import RecipeExecutor

pytestmark = pytest.mark.integration

_PROFILE = {"email": "a@b.com"}
_TEMPLATED_PROFILE = {"target_label": "Node"}


@dataclass(frozen=True)
class Scenario:
    executor: RecipeExecutor
    recipe: AutomationRecipe
    ctx: ExecutionContext


def _recipe(*, goto: str, submit_selector: str = "#submit") -> AutomationRecipe:
    return AutomationRecipe(
        platform="fixture",
        version=1,
        status="active",
        form_hash="h-contract-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal=goto),
            Action(type=ActionType.FILL, selector="#email", value_ref="profile.email"),
            Action(type=ActionType.ASSERT_VISIBLE, selector="#form"),
            Action(type=ActionType.SUBMIT, selector=submit_selector, timeout_ms=300),
        ],
        success_signals=["지원이 완료되었습니다"],
    )


def _write_html(path: Path, body: str) -> str:
    path.write_text(f"<!doctype html><html><body>{body}</body></html>")
    return path.as_uri()


def _templated_click_recipe(*, goto: str) -> AutomationRecipe:
    """CLICK 의 selector 안 '{value}' 가 profile 값으로 치환되는지 (§3)."""
    return AutomationRecipe(
        platform="fixture",
        version=1,
        status="active",
        form_hash="h-contract-2",
        actions=[
            Action(type=ActionType.GOTO, value_literal=goto),
            Action(
                type=ActionType.CLICK,
                selector='button:has-text("{value}")',
                value_ref="profile.target_label",
                timeout_ms=300,
            ),
            Action(
                type=ActionType.ASSERT_VISIBLE,
                selector='#clicked:has-text("node")',
                timeout_ms=300,
            ),
        ],
        success_signals=["지원이 완료되었습니다"],
    )


def _replay_scenarios() -> dict[str, Scenario]:
    clock = SystemClock()
    recipe = _recipe(goto="https://fixture.local/jobs/1")  # replay 는 실제로 열지 않는다
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile=_PROFILE)
    return {
        "ok": Scenario(ReplayExecutor(clock), recipe, ctx),
        "missing_selector": Scenario(
            ReplayExecutor(clock, fail_selectors=frozenset({"#email"})), recipe, ctx
        ),
        "captcha": Scenario(ReplayExecutor(clock, captcha=True), recipe, ctx),
        "unauthenticated": Scenario(
            ReplayExecutor(clock, authed_platforms=frozenset()), recipe, ctx
        ),
        "templated_click": Scenario(
            ReplayExecutor(clock),
            _templated_click_recipe(goto="https://fixture.local/jobs/1"),
            ExecutionContext(application_id="app_1", attempt=1, profile=_TEMPLATED_PROFILE),
        ),
    }


def _playwright_scenarios(tmp_path: Path) -> dict[str, Scenario]:
    clock = SystemClock()
    store = InMemoryBlobStore()
    ctx = ExecutionContext(application_id="app_1", attempt=1, profile=_PROFILE)

    form_html = (
        '<form id="form"><input id="email"/>'
        '<button id="submit" type="button">보내기</button></form>'
    )
    form_url = _write_html(tmp_path / "form.html", form_html)
    captcha_url = _write_html(tmp_path / "captcha.html", '<div class="g-recaptcha"></div>')
    templated_html = (
        '<button id="btn-python" onclick="'
        "document.getElementById('clicked').textContent='python'\">Python</button>"
        '<button id="btn-node" onclick="'
        "document.getElementById('clicked').textContent='node'\">Node</button>"
        '<div id="clicked"></div>'
    )
    templated_url = _write_html(tmp_path / "templated.html", templated_html)

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "fixture.json").write_text('{"cookies": [], "origins": []}')
    empty_auth_dir = tmp_path / "no-auth"
    empty_auth_dir.mkdir()

    def executor(auth_dir: Path) -> PlaywrightExecutor:
        return PlaywrightExecutor(clock, store, auth_dir=auth_dir, headless=True)

    return {
        "ok": Scenario(executor(auth_dir), _recipe(goto=form_url), ctx),
        "missing_selector": Scenario(
            executor(auth_dir), _recipe(goto=form_url, submit_selector="#does-not-exist"), ctx
        ),
        "captcha": Scenario(executor(auth_dir), _recipe(goto=captcha_url), ctx),
        "unauthenticated": Scenario(executor(empty_auth_dir), _recipe(goto=form_url), ctx),
        "templated_click": Scenario(
            executor(auth_dir),
            _templated_click_recipe(goto=templated_url),
            ExecutionContext(application_id="app_1", attempt=1, profile=_TEMPLATED_PROFILE),
        ),
    }


@pytest.fixture(params=["replay", "playwright"])
def scenarios(request: pytest.FixtureRequest, tmp_path: Path) -> dict[str, Scenario]:
    if request.param == "replay":
        return _replay_scenarios()
    return _playwright_scenarios(tmp_path)


async def test_dry_run_stops_before_submit(scenarios: dict[str, Scenario]) -> None:
    s = scenarios["ok"]
    result = await s.executor.run(s.recipe, s.ctx, ExecutionMode.DRY_RUN)
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is None


async def test_live_run_submits(scenarios: dict[str, Scenario]) -> None:
    s = scenarios["ok"]
    result = await s.executor.run(s.recipe, s.ctx, ExecutionMode.LIVE)
    assert result.outcome is AttemptOutcome.SUCCEEDED
    assert result.submitted_at is not None


async def test_missing_selector_raises_recipe_execution_error(
    scenarios: dict[str, Scenario],
) -> None:
    s = scenarios["missing_selector"]
    with pytest.raises(RecipeExecutionError) as exc_info:
        await s.executor.run(s.recipe, s.ctx, ExecutionMode.LIVE)
    assert exc_info.value.form_hash == s.recipe.form_hash
    assert exc_info.value.snapshot_key


async def test_captcha_raises(scenarios: dict[str, Scenario]) -> None:
    s = scenarios["captcha"]
    with pytest.raises(CaptchaEncountered):
        await s.executor.run(s.recipe, s.ctx, ExecutionMode.LIVE)


async def test_unauthenticated_raises(scenarios: dict[str, Scenario]) -> None:
    s = scenarios["unauthenticated"]
    with pytest.raises(AuthRequired):
        await s.executor.run(s.recipe, s.ctx, ExecutionMode.LIVE)


async def test_templated_click_resolves_selector_from_profile(
    scenarios: dict[str, Scenario],
) -> None:
    """CLICK 의 selector 에 심어둔 '{value}' 가 profile 값으로 치환된 뒤 실행된다 — 지원 건마다
    달라지는 텍스트(방금 올린 이력서 파일명, 카테고리별 포트폴리오 파일명 등)로 매칭 대상을
    좁히는 용도(§3)."""
    s = scenarios["templated_click"]
    result = await s.executor.run(s.recipe, s.ctx, ExecutionMode.LIVE)
    assert result.outcome is AttemptOutcome.SUCCEEDED
