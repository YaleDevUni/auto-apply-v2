from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 → 어댑터 선택 (ARCHITECTURE.md §11.4)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "prod"] = "local"

    # 어댑터 선택
    llm_provider: Literal["stub", "anthropic", "claude_cli"] = "stub"
    storage: Literal["local", "memory", "s3"] = "local"
    notifier: Literal["console", "telegram"] = "console"
    resume_engine: Literal["simple", "langgraph"] = "simple"
    executor: Literal["replay", "playwright"] = "replay"
    repository: Literal["memory", "file", "postgres"] = "file"
    playwright_headless: bool = True
    job_source: Literal["fixture", "live"] = "fixture"
    matching_config: Literal["static", "yaml"] = "yaml"
    facts_source: Literal["static", "yaml"] = "yaml"
    profile_source: Literal["static", "yaml"] = "yaml"
    guide_source: Literal["static", "file"] = "file"
    pdf_renderer: Literal["stub", "weasyprint"] = "weasyprint"

    # 인프라
    database_url: str = "postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    data_dir: Path = Path("./var")
    matching_config_path: Path = Path("./config/matching.yaml")
    facts_path: Path = Path("./config/facts.yaml")
    profile_path: Path = Path("./config/profile.yaml")
    resume_guide_path: Path = Path("./config/resume_guide.md")

    # 공고 수집 Schedule (§11.2b) — `cli.py collect-schedule`이 이 값으로 등록/갱신한다.
    job_collection_cron: str = "0 9 * * *"
    job_collection_platforms: str = "wanted,saramin,jasoseol"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "auto-apply"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # 외부 서비스
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # LLM_PROVIDER=claude_cli 일 때만 — API 키 대신 이 머신에 로그인된 Claude Code 구독을 쓴다.
    # `claude login`(또는 `claude setup-token`)이 이미 돼 있어야 한다.
    claude_cli_binary: str = "claude"
    claude_cli_model: str = "claude-sonnet-5"
    claude_cli_max_budget_usd: float = 0.5
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
