"""RepairActivities.propose_recipe_diff — 프롬프트에 실제 실패 사유가 들어가는지 회귀 검증.

재발 방지(메모리 wanted-goto-timeout-misdiagnosed-as-recipe-bug): `req.failure_reason` 대신
`req.form_hash`를 프롬프트에 실어 보내던 버그가 있었다 — LLM이 실패 사유(goto timeout)를 전혀
못 보고 셀렉터만 코스메틱하게 바꿨다. 여기서는 `_ProposePromptSpy`가 실제로 전달된 prompt/
cache_prefix 를 붙잡아 그 사유 문자열이 들어가는지 확인한다.
"""

from pydantic import BaseModel

from auto_apply.activities.repair import RepairActivities
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import ExecutionContext, RepairInput
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.recipe_diagnosis import PageVerdict

PLATFORM = "wanted"


def _previous() -> AutomationRecipe:
    return AutomationRecipe(
        platform=PLATFORM,
        version=8,
        status="active",
        form_hash="h-wanted-1",
        actions=[
            Action(type=ActionType.GOTO, value_literal="https://wanted.co.kr/apply/1"),
            Action(type=ActionType.SUBMIT, selector="#submit"),
        ],
        success_signals=["지원이 완료되었습니다"],
    )


class _PromptSpyLLM:
    """`structured()`에 넘어온 (prompt, cache_prefix) 를 그대로 기록만 하고, actions/

    success_signals 는 previous 를 그대로 돌려줘 스키마 검증만 통과시킨다.
    """

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, prompt: str, *, max_tokens: int = 2048, cache_prefix: str = "") -> str:
        raise NotImplementedError("이 테스트는 structured() 만 쓴다")

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, max_tokens: int = 2048, cache_prefix: str = ""
    ) -> T:
        self.prompts.append((prompt, cache_prefix))
        previous = _previous()
        return schema.model_validate(
            {
                "actions": [a.model_dump(mode="json") for a in previous.actions],
                "success_signals": previous.success_signals,
            }
        )


async def test_propose_recipe_diff_puts_actual_failure_reason_in_prompt() -> None:
    llm = _PromptSpyLLM()
    recipes = InMemoryRecipeSource({PLATFORM: _previous()})
    store = InMemoryBlobStore()
    await store.put("snap/1.html", b"<html><body>form</body></html>")
    activities = RepairActivities(llm, recipes, store)

    reason = "RecipeExecutionError: goto 실패 (): Page.goto: Timeout 5000ms exceeded."
    req = RepairInput(
        platform=PLATFORM,
        form_hash="h-wanted-1",
        snapshot_key="snap/1.html",
        failed_version=8,
        failure_reason=reason,
        ctx=ExecutionContext(application_id="app_1", attempt=1),
    )

    await activities.propose_recipe_diff(req)

    assert llm.prompts, "structured() 가 한 번은 불려야 한다"
    prompt, cache_prefix = llm.prompts[0]
    full_prompt = cache_prefix + prompt
    assert reason in full_prompt
    # form_hash 를 실패 사유 대신 프롬프트에 흘려보내던 옛 버그가 재발하지 않았는지 확인 —
    # form_hash 문자열 자체가 [실패 사유] 자리에 잘못 들어가면 안 된다.
    assert "[실패 사유]\nh-wanted-1" not in full_prompt


async def test_propose_recipe_diff_skips_llm_for_goto_timeout_with_action_index() -> None:
    """설계 확정(2026-08-23): goto timeout 은 코드가 결정론적으로 timeout_ms 만 올리고 LLM

    호출 자체를 생략한다 — "timeout 이면 selector 건드리지 마라"를 프롬프트 지시로 LLM에게
    맡기지 않기로 한 결정의 대체 구현이다.
    """
    llm = _PromptSpyLLM()
    recipes = InMemoryRecipeSource({PLATFORM: _previous()})
    store = InMemoryBlobStore()
    activities = RepairActivities(llm, recipes, store)

    reason = "RecipeExecutionError: goto 실패 (): Page.goto: Timeout 5000ms exceeded."
    req = RepairInput(
        platform=PLATFORM,
        form_hash="h-wanted-1",
        snapshot_key="snap/1.html",  # bump 경로는 스냅샷을 안 읽으니 store 에 없어도 된다
        failed_version=8,
        failure_reason=reason,
        failed_action_index=0,  # actions[0] 이 goto
        ctx=ExecutionContext(application_id="app_1", attempt=1),
    )

    result = await activities.propose_recipe_diff(req)

    assert not llm.prompts, "goto timeout 은 LLM 을 아예 호출하지 않아야 한다"
    assert result.candidate.actions[0].timeout_ms == 10_000
    assert result.candidate.actions[1] == _previous().actions[1]


async def test_propose_recipe_diff_falls_back_when_reason_missing() -> None:
    """failure_reason 이 비어 있어도(옛 호출부·테스트 등) 최소한 빈 문자열/form_hash 오염 없이

    안전한 fallback 문구를 넣는다.
    """
    llm = _PromptSpyLLM()
    recipes = InMemoryRecipeSource({PLATFORM: _previous()})
    store = InMemoryBlobStore()
    await store.put("snap/1.html", b"<html></html>")
    activities = RepairActivities(llm, recipes, store)

    req = RepairInput(
        platform=PLATFORM,
        form_hash="h-wanted-1",
        snapshot_key="snap/1.html",
        failed_version=8,
        ctx=ExecutionContext(application_id="app_1", attempt=1),
    )

    await activities.propose_recipe_diff(req)

    prompt, cache_prefix = llm.prompts[0]
    full_prompt = cache_prefix + prompt
    assert "[실패 사유]\n(사유 미상)" in full_prompt


# ── §2.4a 판정 activity ─────────────────────────────────────────────────
_ALREADY_APPLIED_PAGE = (
    "<html><body><p>" + "본 채용정보는 무단전재 금지. " * 40 + "</p>"
    "<button>지원완료</button></body></html>"
).encode()


def _activities(store: InMemoryBlobStore) -> RepairActivities:
    return RepairActivities(_PromptSpyLLM(), InMemoryRecipeSource({PLATFORM: _previous()}), store)


def _diag_req(snapshot_key: str, reason: str = "wait_for 실패 (text=첨부파일 선택)") -> RepairInput:
    return RepairInput(
        platform=PLATFORM,
        form_hash="h-wanted-1",
        snapshot_key=snapshot_key,
        failed_version=8,
        failure_reason=reason,
        ctx=ExecutionContext(application_id="app_1", attempt=1),
    )


async def test_diagnose_reports_already_applied_page() -> None:
    """실측 사고(2026-08-24) 재현 — 이 판정이 사람에게 그대로 보여야 ❌ 를 고를 수 있다."""
    store = InMemoryBlobStore()
    await store.put("snap/applied.html", _ALREADY_APPLIED_PAGE)

    diagnosis = await _activities(store).diagnose_recipe_failure(_diag_req("snap/applied.html"))

    assert diagnosis.verdict is PageVerdict.ALREADY_APPLIED
    assert "이미 지원한 공고" in diagnosis.summary


async def test_diagnose_survives_missing_snapshot() -> None:
    """스냅샷 조회 실패가 확인 절차를 막으면 안 된다 — 판정만 "페이지 없음"으로 떨어진다."""
    diagnosis = await _activities(InMemoryBlobStore()).diagnose_recipe_failure(_diag_req("gone"))
    assert diagnosis.verdict is PageVerdict.PAGE_NOT_LOADED


async def test_diff_prompt_carries_the_page_diagnosis() -> None:
    """LLM 도 같은 판정을 본다 — 멀쩡한 selector 를 헤집지 말라는 근거로 쓰인다."""
    store = InMemoryBlobStore()
    await store.put("snap/applied.html", _ALREADY_APPLIED_PAGE)
    llm = _PromptSpyLLM()
    activities = RepairActivities(llm, InMemoryRecipeSource({PLATFORM: _previous()}), store)

    await activities.propose_recipe_diff(_diag_req("snap/applied.html"))

    prompt, cache_prefix = llm.prompts[0]
    assert "[페이지 판정]" in cache_prefix + prompt
    assert "이미 지원한 공고" in cache_prefix + prompt
