"""API 요청/응답 모델. HTTP 경계 전용 — workflow payload(contracts/dto.py)와는 다른 계층이다.

예: `application_id` 는 서버가 발급한다(idgen) — 클라이언트가 직접 workflow_id 를
고를 수 있게 하면 §2.1 의 멱등성 키 설계가 깨진다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ApplicationState


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartApplicationRequest(_Model):
    user_id: str
    job_url: str


class StartApplicationResponse(_Model):
    application_id: str
    workflow_id: str


class ApplicationView(_Model):
    """§4.1 — state 는 워크플로우 query 가 원본, history 는 DB projection."""

    application_id: str
    state: ApplicationState
    scheduled_at: datetime | None = None
    attempts: int = 0
    history: list[PersistState] = Field(default_factory=list)


class RecipeVersionsResponse(_Model):
    """§7 GET /recipes/{platform} — RecipeSource.versions() 그대로, version 오름차순."""

    platform: str
    versions: list[AutomationRecipe]


class PromoteRecipeRequest(_Model):
    version: int
