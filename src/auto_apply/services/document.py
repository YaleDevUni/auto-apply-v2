"""DocumentService — 공고맞춤 이력서 생성 (§A7).

v2 ResumeWorkflow 의 생성 → 검토(ground_check) → 재생성 루프를 Temporal 없이 옮겼다 (D4).
LLM 호출 재시도는 여기서 하지 않는다 — 인프라성 실패 재시도는 JobRunner 몫이다 (§A9).
"""

import structlog

from auto_apply.contracts.dto import GenerateResumeRequest, RenderedPdf, ResumeDraft, ReviewRequest
from auto_apply.domain.errors import AutoApplyError
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer

log = structlog.get_logger(__name__)

MAX_REVIEW_ROUNDS = 3


class ResumeReviewExhausted(AutoApplyError):
    """검토를 `max_review_rounds` 번 통과하지 못했다 — 없는 사실을 쓰느니 실패한다 (절대 규칙 4)."""

    def __init__(self, rounds: int, issues: list[str]) -> None:
        super().__init__(f"검토 {rounds}회 실패: {issues}")
        self.rounds = rounds
        self.issues = issues


class DocumentService:
    def __init__(
        self,
        generator: ResumeGenerator,
        reviewer: ResumeReviewer,
        pdf: PdfRenderer,
        *,
        max_review_rounds: int = MAX_REVIEW_ROUNDS,
    ) -> None:
        self._generator = generator
        self._reviewer = reviewer
        self._pdf = pdf
        self._max_review_rounds = max_review_rounds

    async def generate_resume(
        self, req: GenerateResumeRequest, *, run_id: str | None = None
    ) -> ResumeDraft:
        """검토를 통과한 초안만 돌려준다. 통과 못 하면 `ResumeReviewExhausted`."""
        bound = log.bind(application_id=req.application_id, run_id=run_id)
        issues: list[str] = []
        for round_no in range(1, self._max_review_rounds + 1):
            draft = await self._generator.generate(req)
            verdict = await self._reviewer.review(
                ReviewRequest(draft=draft, job=req.job, user_id=req.user_id)
            )
            if verdict.passed:
                bound.info("resume.review_passed", round=round_no, resume_id=draft.resume_id)
                return draft
            issues = verdict.issues
            bound.info("resume.review_failed", round=round_no, issues=issues)
        raise ResumeReviewExhausted(self._max_review_rounds, issues)

    async def render_pdf(self, draft: ResumeDraft) -> RenderedPdf:
        return await self._pdf.render(draft)
