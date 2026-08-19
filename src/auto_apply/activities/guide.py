"""이력서 가이드 patch 제안/적용 activity (REVISE/general, domain/guide_patch.py).

LLM 은 `{old, new}` 치환 쌍만 낸다 — 전문을 다시 쓰게 하면 지시 안 한 다른 규칙이 조용히
사라질 수 있어서다. 실제 텍스트 교체는 domain/guide_patch.py 의 순수 함수가 한다.
"""

from collections.abc import Callable
from typing import Any

from temporalio import activity

from auto_apply.ai.prompts import build_guide_patch_prompt
from auto_apply.ai.schemas import GuidePatchSchema
from auto_apply.contracts.dto import GuidePatchProposal, ProposeGuidePatchRequest
from auto_apply.domain.guide_patch import apply_patch
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.llm import LLMClient


class GuideActivities:
    def __init__(self, llm: LLMClient, guide: GuideSource) -> None:
        self._llm = llm
        self._guide = guide

    @activity.defn(name="propose_guide_patch")
    async def propose_guide_patch(self, req: ProposeGuidePatchRequest) -> GuidePatchProposal:
        text = await self._guide.get()
        prompt = build_guide_patch_prompt(text, req.feedback, req.job)
        # 이 activity 는 재시도/재프롬프트 루프가 없는 단발 호출이라 재사용할 캐시 경계가
        # 없다 — cache_prefix 를 안 넘긴다([[claude-cli-prompt-cache-redesign]]).
        out = await self._llm.structured(prompt, GuidePatchSchema)
        return GuidePatchProposal(old=out.old, new=out.new, rationale=out.rationale)

    @activity.defn(name="apply_guide_patch")
    async def apply_guide_patch(self, patch: GuidePatchProposal) -> None:
        """사람이 diff 를 이미 승인한 뒤에만 호출된다 (workflows/_revision.py)."""
        text = await self._guide.get()
        patched = apply_patch(text, patch.old, patch.new)
        await self._guide.save(patched)

    def all(self) -> list[Callable[..., Any]]:
        return [self.propose_guide_patch, self.apply_guide_patch]
