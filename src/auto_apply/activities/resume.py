from collections.abc import Callable
from typing import Any

from temporalio import activity

from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    RenderedPdf,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer


class ResumeActivities:
    def __init__(
        self, generator: ResumeGenerator, reviewer: ResumeReviewer, pdf: PdfRenderer
    ) -> None:
        self._generator = generator
        self._reviewer = reviewer
        self._pdf = pdf

    @activity.defn(name="generate_resume")
    async def generate_resume(self, req: GenerateResumeRequest) -> ResumeDraft:
        return await self._generator.generate(req)

    @activity.defn(name="review_resume")
    async def review_resume(self, req: ReviewRequest) -> ReviewVerdict:
        return await self._reviewer.review(req)

    @activity.defn(name="render_pdf")
    async def render_pdf(self, draft: ResumeDraft) -> RenderedPdf:
        return await self._pdf.render(draft)

    def all(self) -> list[Callable[..., Any]]:
        return [self.generate_resume, self.review_resume, self.render_pdf]
