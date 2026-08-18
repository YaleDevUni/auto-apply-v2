"""plain 함수 기반 이력서 생성/검토.

LangGraph 는 이 그래프가 실제로 분기·병렬로 복잡해지는 시점(M3)에 같은 port 로 교체한다 (§9.2).
"""

from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.ports.clock import IdGen
from auto_apply.ports.llm import LLMClient


class SimpleResumeGenerator:
    def __init__(self, llm: LLMClient, idgen: IdGen) -> None:
        self._llm = llm
        self._idgen = idgen

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        summary = await self._llm.complete(
            f"{req.job.company} / {req.job.title} 공고에 맞춘 요약을 작성하라."
        )
        return ResumeDraft(
            resume_id=self._idgen.new_id("res"),
            content={"summary": summary, "job_id": req.job.job_id},
            used_fact_ids=[],
        )


class SimpleResumeReviewer:
    """M1 검토 게이트: 사실 근거 없는 초안을 통과시키지 않는 자리만 잡아둔다.

    실제 hallucination 판정은 Fact 저장소가 생기는 M3 에서 채운다.
    """

    def __init__(self, *, min_summary_len: int = 10) -> None:
        self._min_summary_len = min_summary_len

    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        summary = str(req.draft.content.get("summary", ""))
        issues: list[str] = []
        if len(summary) < self._min_summary_len:
            issues.append("summary 가 너무 짧다")
        return ReviewVerdict(passed=not issues, score=1.0 if not issues else 0.0, issues=issues)
