"""PortfolioMap — "이 지원자의 직무 카테고리별로 어느 첨부파일을 쓰는가"의 유일한 원천.

`Profile`과 같은 이유로 결정론 코드가 다루는 정형 정보다 — LLM은 `job_category`(카테고리
라벨 하나)만 고르고, 그 라벨을 실제 첨부파일명으로 바꾸는 건 이 매핑 + 코드가 한다
(`adapters/resume/_assemble.py`). "AI는 생성만, 판정·조합은 코드" 원칙의 연장.
"""

from pydantic import BaseModel, ConfigDict, Field


class PortfolioMap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    # 카테고리 라벨(예: "개발자", "AX개발자", "데브옵스") -> 실제 첨부파일명.
    # 파일명은 플랫폼이 화면에 그대로 보여주는 이름과 일치해야 selector 로 다시 찾을 수 있다.
    categories: dict[str, str] = Field(default_factory=dict)
