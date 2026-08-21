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
    # SUPERVISED 페이지 경계 체크포인트 승인/거절을 어디 남길지 (§ supervised-checkpoint-design).
    # nonce 발급 프로세스(worker activity)와 승인 프로세스(webhook/리스너)가 갈라져서
    # in-process 상태로는 공유가 안 된다.
    checkpoint_store: Literal["file", "memory"] = "file"
    # 태그(REVISE/가이드 patch 답장) 없는 자유 텍스트를 telegram/agent.py 의 채팅 에이전트로
    # 넘길지. 끄면 예전 동작(조용히 무시)으로 정확히 되돌아간다 — LLM 비용/예산
    # (CLAUDE_CLI_MAX_BUDGET_USD)이나 예상 밖 동작이 우려되면 재배포 없이 끌 수 있는 손잡이.
    telegram_chat_agent_enabled: bool = True
    # 채팅 에이전트의 "도구를 부를지/답할지" 판단은 이력서 생성보다 훨씬 가벼운 분류 작업이라
    # 별도로 싼 모델을 쓴다(LLMClient 는 인스턴스당 모델 하나 — bootstrap 이 llm 과 별개로
    # chat_llm 을 이 모델로 한 번 더 만든다). llm_provider=stub 이면 무시된다.
    telegram_agent_model: str = "claude-haiku-4-5-20251001"
    # telegram/agent.py 의 start_applications 도구가 새 지원을 시작할 때 쓰는 user_id.
    # 이 프로젝트는 단일 사용자 전제라 config/profile.yaml 의 user_id 와 맞춰 고정값으로 둔다.
    default_user_id: str = "u1"

    # 인프라
    database_url: str = "postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply"
    # postgres contract test 전용 DB — TRUNCATE 로 상태를 비우므로 운영 database_url 과
    # 반드시 분리한다 (postgres-integration-test-data-wipe-hazard, db-init/ 참고).
    test_database_url: str = (
        "postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply_test"
    )
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

    # 워크플로우 감시 watchdog (`make watchdog`, workflow-failure-visibility-backlog §3) —
    # FAILED/TERMINATED/TIMED_OUT 으로 끝난 워크플로우를 능동으로 텔레그램 알림한다.
    watchdog_poll_interval_seconds: int = Field(default=60, ge=5)
    watchdog_lookback_minutes: int = Field(default=60, ge=1)

    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "auto-apply"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    # S3BlobStore contract test 전용 버킷 — TRUNCATE 대신 매 테스트 전 전체 object 삭제라
    # postgres-integration-test-data-wipe-hazard 와 같은 이유로 운영 s3_bucket 과 분리한다.
    s3_test_bucket: str = "auto-apply-test"

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
    # scripts/auto_login.py 전용 — 2026-08-21 정책 변경(CLAUDE.md "자동 로그인 정책" 참고).
    # var/auth/{platform}.json 세션이 만료됐을 때 사람이 매번 수동 로그인하는 대신 이 계정으로
    # 자동 재로그인한다. CAPTCHA/추가 인증은 여전히 우회하지 않고 사람에게 넘긴다
    # (domain/login_flow.detect_login_outcome).
    saramin_username: str = ""
    saramin_password: str = ""

    # 안전장치 (§9.5)
    dry_run_only: bool = True
    approval_timeout_hours: int = Field(default=72, ge=1)
    # 이 라운드를 넘으면 사람에게 넘긴다 — 무한 재생성 루프를 만들지 않는다 (workflows/_revision.py)
    max_revisions: int = Field(default=10, ge=1)
    # 가이드 patch 제안 자체에 대한 💬 코멘트 재시도 한도
    max_guide_revisions: int = Field(default=5, ge=1)

    # 이력서 블록 개수 안전 상한 (domain/resume_blocks.select_relevant_blocks). 실제 "몇 개
    # 보여줄지"는 더 이상 이 값이 아니라 config/resume_guide.{platform}.md + LLM 판단이 정한다
    # (2026-08-21, 사용자 결정 — 예전엔 이 값 자체가 UX 레버였는데, 가이드 patch로 개수를 못
    # 바꾼다는 걸 실측하고 여기로 뺐었다[resume-block-count-cap]. 다시 가이드로 옮기면서 이
    # 값은 "프롬프트 폭주 방지용 안전판"으로만 남긴다 — 한 회사/개인 프로젝트에 fact가 비정상
    # 적으로 많이 쌓였을 때(config/facts.yaml 오타 등)를 대비한 상한이라 실사용 범위보다
    # 넉넉하게 잡는다.
    resume_max_project_blocks: int = Field(default=20, ge=1)
    resume_max_career_blocks_per_entity: int = Field(default=20, ge=1)

    # SUPERVISED 체크포인트 대기 한도 — "사람이 실시간으로 지켜보고 있다"는 전제라 짧게 잡는다
    # (§ supervised-checkpoint-design). 넘기거나 거절되면 CheckpointDeclined → needs_human.
    checkpoint_timeout_minutes: int = Field(default=30, ge=1)
    checkpoint_poll_seconds: int = Field(default=5, ge=1)

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        raw = (c.strip() for c in self.telegram_allowed_chat_ids.split(","))
        return frozenset(int(c) for c in raw if c)


def load_settings() -> Settings:
    return Settings()
