"""실제 PDF 렌더러 (ARCHITECTURE.md §9.1 — WeasyPrint 를 PdfRenderer 후보로 명시했던 결정).

macOS(Homebrew) 환경에서 weasyprint 가 요구하는 libgobject/pango/cairo 를 dlopen 하는데,
이 프로세스의 환경에 `DYLD_FALLBACK_LIBRARY_PATH`가 없으면 macOS 가 그 경로를 걸러내서
import 시점에 OSError 로 죽는다(셸 프로파일에 넣게 하는 대신 여기서 프로세스 시작 시 한 번
보정한다 — 사용자 셸 설정에 기대면 `make api`/`make worker`가 새 셸에서 조용히 깨진다).
"""

import os
import sys

if sys.platform == "darwin":  # pragma: no cover — macOS 전용 보정, 다른 OS 는 no-op
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib:/usr/local/lib:/usr/lib")

import asyncio

import weasyprint

from auto_apply.adapters.pdf._template import render_resume_html
from auto_apply.contracts.dto import RenderedPdf, ResumeDraft
from auto_apply.contracts.resume_content import AssembledResume
from auto_apply.domain.resume_cleanup import build_resume_filename
from auto_apply.ports.storage import BlobStore


class WeasyPrintPdfRenderer:
    def __init__(self, store: BlobStore) -> None:
        self._store = store

    async def render(self, draft: ResumeDraft) -> RenderedPdf:
        resume = AssembledResume.model_validate(draft.content)
        html = render_resume_html(resume)
        # weasyprint 는 동기 CPU 바운드 라이브러리다 — 이벤트 루프를 막지 않게 스레드로 뺀다.
        pdf_bytes = await asyncio.to_thread(lambda: weasyprint.HTML(string=html).write_pdf())
        # 채용담당자가 업로드 목록에서 이력서/포트폴리오를 구별할 수 있게 사람이 알아볼 수
        # 있는 이름으로 짓는다(실행기가 blob key 마지막 경로 요소를 업로드 파일명으로 그대로
        # 쓴다 — playwright.py._resolve_upload). 해시 접미사는 resume_cleanup 이 자동 생성물을
        # 골라 지우는 근거라 build_resume_filename() 한 곳에서만 짓는다.
        key = f"resumes/{build_resume_filename(resume.name, draft.resume_id)}"
        await self._store.put(key, pdf_bytes, content_type="application/pdf")
        return RenderedPdf(blob_key=key, bytes_written=len(pdf_bytes))
