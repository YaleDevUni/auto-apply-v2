"""ResumeContentSchema 회귀 — T0.1 에서 v2 포트폴리오 카테고리 필드(job_category)를 뺐다.

extra=forbid 라 LLM 이 옛 필드를 내면 스키마 위반으로 재프롬프트된다 — 조용히 무시되지 않는다.
"""

import pytest
from pydantic import ValidationError

from auto_apply.ai.schemas import ResumeContentSchema


def test_minimal_payload_is_valid() -> None:
    out = ResumeContentSchema.model_validate({"summary": "요약"})

    assert out.caution_notes == []


def test_removed_job_category_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResumeContentSchema.model_validate({"summary": "요약", "job_category": "개발자"})
