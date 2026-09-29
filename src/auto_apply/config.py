from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 → 어댑터 선택 (bootstrap.py, §A2)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "prod"] = "local"

    # 어댑터 선택
    llm_provider: Literal["stub", "anthropic", "claude_cli"] = "stub"
    storage: Literal["local", "memory"] = "local"
    resume_engine: Literal["simple"] = "simple"
    repository: Literal["sqlite", "memory"] = "sqlite"
    facts_source: Literal["static", "yaml"] = "yaml"
    profile_source: Literal["static", "yaml"] = "yaml"
    guide_source: Literal["static", "file"] = "file"

    # 저장소 (D3·D4: SQLite + 로컬 파일. platformdirs 데이터 디렉터리 전환은 T0.3)
    data_dir: Path = Path("./var")
    facts_path: Path = Path("./config/facts.yaml")
    profile_path: Path = Path("./config/profile.yaml")
    resume_guide_dir: Path = Path("./config")  # resume_guide.{platform}.md 를 이 안에서 찾는다

    # 외부 서비스
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # LLM_PROVIDER=claude_cli 일 때만 — API 키 대신 이 머신에 로그인된 Claude Code 구독을 쓴다.
    # `claude login`(또는 `claude setup-token`)이 이미 돼 있어야 한다.
    claude_cli_binary: str = "claude"
    claude_cli_model: str = "claude-sonnet-5"
    claude_cli_max_budget_usd: float = 0.5

    # 안전장치 (§9.5)
    dry_run_only: bool = True
    approval_timeout_hours: int = Field(default=72, ge=1)
    # 이 라운드를 넘으면 사람에게 넘긴다 — 무한 재생성 루프를 만들지 않는다
    max_revisions: int = Field(default=10, ge=1)

    # 이력서 블록 개수 안전 상한 (domain/resume_blocks.select_relevant_blocks). 실제 "몇 개
    # 보여줄지"는 더 이상 이 값이 아니라 config/resume_guide.{platform}.md + LLM 판단이 정한다
    # (2026-08-21, 사용자 결정 — 예전엔 이 값 자체가 UX 레버였는데, 가이드 patch로 개수를 못
    # 바꾼다는 걸 실측하고 여기로 뺐었다[resume-block-count-cap]. 다시 가이드로 옮기면서 이
    # 값은 "프롬프트 폭주 방지용 안전판"으로만 남긴다 — 한 회사/개인 프로젝트에 fact가 비정상
    # 적으로 많이 쌓였을 때(config/facts.yaml 오타 등)를 대비한 상한이라 실사용 범위보다
    # 넉넉하게 잡는다.
    resume_max_project_blocks: int = Field(default=20, ge=1)
    resume_max_career_blocks_per_entity: int = Field(default=20, ge=1)

    # 웹 콘솔 프론트엔드(Vite dev 서버)가 cross-origin 으로 API 를 부를 수 있게 허용하는
    # origin. 이 콘솔은 인증 계층이 없다(사용자 결정 — 로컬/사설망 전용 전제) — 그래서 CORS 도
    # 와일드카드가 아니라 이 값 하나만 명시적으로 허용한다.
    web_cors_origin: str = "http://localhost:5173"

    @property
    def database_url(self) -> str:
        """DB 는 데이터 디렉터리 안의 파일 하나다 (§A1) — 따로 설정할 값이 아니라 파생값이다."""
        return f"sqlite+aiosqlite:///{(self.data_dir / 'db.sqlite3').resolve().as_posix()}"


def load_settings() -> Settings:
    return Settings()
