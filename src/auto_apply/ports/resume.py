from typing import Protocol

from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)


class ResumeGenerator(Protocol):
    """공고맞춤 이력서 생성 교체 지점 (§A7)."""

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft: ...


class ResumeReviewer(Protocol):
    async def review(self, req: ReviewRequest) -> ReviewVerdict: ...
