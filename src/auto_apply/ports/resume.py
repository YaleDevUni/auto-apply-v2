from typing import Protocol

from auto_apply.contracts.dto import GenerateResumeRequest, ResumeDraft, ReviewVerdict


class ResumeGenerator(Protocol):
    """simple(plain 함수) → langgraph(M3) 교체 지점 (§9.2)."""

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft: ...


class ResumeReviewer(Protocol):
    async def review(self, draft: ResumeDraft, req: GenerateResumeRequest) -> ReviewVerdict: ...
