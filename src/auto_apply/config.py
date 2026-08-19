from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 → 어댑터 선택 (ARCHITECTURE.md §11.4)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "prod"] = "local"

    # 어댑터 선택
    llm_provider: Literal["stub", "anthropic"] = "stub"
    storage: Literal["local", "memory", "s3"] = "local"
    notifier: Literal["console", "telegram"] = "console"
    resume_engine: Literal["simple", "langgraph"] = "simple"
    executor: Literal["replay", "playwright"] = "replay"
    repository: Literal["memory", "file", "postgres"] = "file"
    playwright_headless: bool = True

    # 인프라
    database_url: str = "postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    data_dir: Path = Path("./var")

    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "auto-apply"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # 외부 서비스
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""

    # 안전장치 (§9.5)
    dry_run_only: bool = True
    approval_timeout_hours: int = Field(default=72, ge=1)

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        raw = (c.strip() for c in self.telegram_allowed_chat_ids.split(","))
        return frozenset(int(c) for c in raw if c)


def load_settings() -> Settings:
    return Settings()
