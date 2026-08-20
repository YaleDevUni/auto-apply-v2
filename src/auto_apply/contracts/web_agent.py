"""WebAgentExecutor 입출력 타입 (ARCHITECTURE.md §2.4b).

`ExecuteInput`/`ExecutionResult`(contracts/dto.py)와 대구를 이루지만 별도 파일로 뒀다 — Recipe
실행과 개념이 다르다(사전 검증된 액션 리스트가 아니라 자연어 task + 데이터). `Credential`은
여기 없다 — ports/credentials.py 참고, 활동 경계를 넘지 않는 값이라 workflow-safe DTO 자리인
이 파일에 두면 안 된다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.resume_content import AssembledResume
from auto_apply.domain.enums import AttemptOutcome


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WebAgentTask(_Frozen):
    application_id: str
    apply_url: str
    resume: AssembledResume  # 웹폼 필드 채우기용 — 이력서 파이프라인(§2.3) 산출물 재사용
    resume_pdf_key: str = ""  # PDF 업로드가 필요한 폼 대비
    essay_answers: dict[str, str] = Field(default_factory=dict)  # 자소서 파이프라인 연결점(추후)
    credential_key: str | None = None  # CredentialSource 조회 키(회사명). 값 자체는 여기 안 실림


class WebAgentSessionRef(_Frozen):
    """`fill()` → `submit()`으로 이어주는 불투명 핸들. 어댑터 내부 세션 id 를 감싼다."""

    session_id: str


class WebAgentFillResult(_Frozen):
    session: WebAgentSessionRef
    screenshot_key: str  # 사람 승인 메시지에 첨부할 스크린샷(BlobStore)
    summary: str  # 무엇을 채웠는지 요약 — 승인 메시지 본문


class WebAgentSubmitResult(_Frozen):
    outcome: AttemptOutcome
    submitted_at: datetime | None = None
    detail: str = ""
