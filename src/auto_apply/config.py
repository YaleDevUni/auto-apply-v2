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
    executor: Literal["replay", "playwright", "agent_browser"] = "replay"
    repository: Literal["memory", "file", "postgres"] = "file"
    playwright_headless: bool = True
    # EXECUTOR=agent_browser 일 때만 — 이 머신에 `npm i -g agent-browser && agent-browser install`
    # 이 돼 있어야 한다. headless 는 playwright_headless 를 그대로 공유한다(같은 의미의 설정을
    # executor 별로 중복시키지 않는다).
    agent_browser_binary: str = "agent-browser"
    job_source: Literal["fixture", "live"] = "fixture"
    matching_config: Literal["static", "yaml"] = "yaml"
    facts_source: Literal["static", "yaml"] = "yaml"
    profile_source: Literal["static", "yaml"] = "yaml"
    portfolio_source: Literal["static", "yaml"] = "yaml"
    guide_source: Literal["static", "file"] = "file"
    pdf_renderer: Literal["stub", "weasyprint"] = "weasyprint"
    web_agent: Literal["replay", "aside_cli"] = "replay"
    credential_source: Literal["static", "json"] = "static"

    # 인프라
    database_url: str = "postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    data_dir: Path = Path("./var")
    matching_config_path: Path = Path("./config/matching.yaml")
    facts_path: Path = Path("./config/facts.yaml")
    profile_path: Path = Path("./config/profile.yaml")
    portfolio_map_path: Path = Path("./config/portfolio_map.yaml")
    resume_guide_dir: Path = Path("./config")  # resume_guide.{platform}.md 를 이 안에서 찾는다
    credential_queue_path: Path = Path("./config/credentials.json")
    # WEB_AGENT=aside_cli 일 때만 — 이 머신에 aside CLI가 설치되고 로그인돼 있어야 한다.
    aside_cli_binary: str = "aside"
    aside_cli_account: str = ""

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
    # 이 라운드를 넘으면 사람에게 넘긴다 — 무한 재생성 루프를 만들지 않는다 (workflows/_revision.py)
    max_revisions: int = Field(default=10, ge=1)
    # 가이드 patch 제안 자체에 대한 💬 코멘트 재시도 한도
    max_guide_revisions: int = Field(default=5, ge=1)

    # 이력서 블록 개수 상한 (domain/resume_blocks.select_relevant_blocks) — 자연어 가이드
    # patch 로는 못 바꾸는 숫자값이라 여기 둔다. 사람이 config/facts.yaml 을 고치지 않고도
    # "회사 하나에 세부 항목이 너무 많다" 같은 피드백을 반영할 수 있는 유일한 통로다.
    resume_max_project_blocks: int = Field(default=3, ge=1)
    resume_max_career_blocks_per_entity: int = Field(default=4, ge=1)

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        raw = (c.strip() for c in self.telegram_allowed_chat_ids.split(","))
        return frozenset(int(c) for c in raw if c)


def load_settings() -> Settings:
    return Settings()
