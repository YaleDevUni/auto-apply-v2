"""Fact — 이력서 생성의 유일한 사실 원천 (ARCHITECTURE.md §4).

Recipe/MatchingConfig 와 같은 이유로 코드가 아니라 데이터다: `config/facts.yaml`이 원본이고
이 파일은 그 스키마만 정의한다. `keywords`는 TrackRule.keywords 와 같은 이유로 둔다 — 자유
텍스트(content)를 job 설명과 직접 매칭하면 실패하기 쉬워서, 매칭용 신호를 명시적 필드로 뺐다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(_Frozen):
    id: str
    user_id: str
    kind: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    source: str = ""
    verified_at: datetime | None = None
