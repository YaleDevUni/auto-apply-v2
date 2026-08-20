"""Recipe 수선 activity (§2.4). LLM diff 제안 + I/O(스냅샷 조회, 버전 저장/승격)만 담당한다.

정책 검증(node D, domain/recipe_policy.py)은 순수 함수라 activity 없이 workflow 가 직접
부른다 — `propose_recipe_diff`가 `previous`도 같이 돌려주는 이유가 그거다(§11.3).
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from temporalio import activity

from auto_apply.ai.prompts import build_recipe_diff_prompt, reprompt_error_suffix
from auto_apply.ai.schemas import RecipeDiffSchema
from auto_apply.contracts.dto import PromoteRecipeInput, RecipeDiffResult, RepairInput
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import AutoApplyError, LLMSchemaViolation, PolicyViolation
from auto_apply.domain.recipe_repair import build_candidate_recipe
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.recipe_source import RecipeSource
from auto_apply.ports.storage import BlobStore


class RepairActivities:
    def __init__(
        self,
        llm: LLMClient,
        recipes: RecipeSource,
        store: BlobStore,
        *,
        max_reprompts: int = 2,
    ) -> None:
        self._llm = llm
        self._recipes = recipes
        self._store = store
        self._max_reprompts = max_reprompts

    @activity.defn(name="propose_recipe_diff")
    async def propose_recipe_diff(self, req: RepairInput) -> RecipeDiffResult:
        versions = await self._recipes.versions(req.platform)
        previous = next((v for v in versions if v.version == req.failed_version), None)
        if previous is None:
            # 정상 경로라면 절대 안 나야 한다 — req.failed_version 은 방금 그 버전을 실제로
            # 실행하다 실패한 곳에서 왔다. 재시도해도 같은 버전은 계속 없으니 non-retryable.
            raise PolicyViolation(f"{req.platform} v{req.failed_version}: 이전 버전을 찾을 수 없다")
        next_version = max((v.version for v in versions), default=req.failed_version) + 1

        try:
            snapshot_html = (await self._store.get(req.snapshot_key)).decode(
                "utf-8", errors="replace"
            )
        except AutoApplyError:
            snapshot_html = "(스냅샷을 불러오지 못했다)"

        prompt = build_recipe_diff_prompt(previous, snapshot_html, req.form_hash)
        candidate = await self._propose_with_reprompt(prompt, previous, next_version)
        return RecipeDiffResult(candidate=candidate, previous=previous)

    async def _propose_with_reprompt(
        self, prompt: str, previous: AutomationRecipe, version: int
    ) -> AutomationRecipe:
        # SimpleResumeGenerator._structured_with_reprompt 와 같은 패턴 — 원본 prompt 를
        # cache_prefix 로 고정해 재시도 전체가 같은 캐시 경계를 공유하게 한다.
        addition = ""
        last_error: LLMSchemaViolation | None = None
        for _ in range(self._max_reprompts + 1):
            try:
                diff = await self._llm.structured(addition, RecipeDiffSchema, cache_prefix=prompt)
                return build_candidate_recipe(
                    previous,
                    actions=diff.actions,
                    success_signals=diff.success_signals,
                    version=version,
                )
            except LLMSchemaViolation as e:
                last_error = e
                addition = reprompt_error_suffix(str(e))
            except ValidationError as e:
                # actions/success_signals 는 각각 유효해도 조립된 AutomationRecipe 전체는
                # 무효일 수 있다(예: submit 이 마지막이 아님) — 이것도 스키마 위반으로
                # 취급해 재프롬프트한다(§2.4 node C).
                last_error = LLMSchemaViolation(f"AutomationRecipe: {e}")
                addition = reprompt_error_suffix(str(last_error))
        assert last_error is not None  # for 루프가 최소 1회 돌아 반드시 세팅된다
        raise last_error

    @activity.defn(name="save_recipe_candidate")
    async def save_recipe_candidate(self, recipe: AutomationRecipe) -> AutomationRecipe:
        return await self._recipes.save(recipe)

    @activity.defn(name="promote_recipe")
    async def promote_recipe(self, req: PromoteRecipeInput) -> AutomationRecipe:
        return await self._recipes.promote(req.platform, req.version)

    def all(self) -> list[Callable[..., Any]]:
        return [self.propose_recipe_diff, self.save_recipe_candidate, self.promote_recipe]
