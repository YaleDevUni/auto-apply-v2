import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "auto-apply"
# appauthor=False: Windows 에서 `%LOCALAPPDATA%\auto-apply\auto-apply` 처럼 이름이 두 번 붙지 않게.
DEFAULT_DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))
# 사용자 설정 디렉터리를 옮기는 환경변수 — 테스트 격리용이자, platformdirs 경로를 못 쓰는 환경용.
CONFIG_DIR_ENV = "AUTO_APPLY_CONFIG_DIR"
DEV_MODE_ENV = "AUTO_APPLY_DEV"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
# 개발 모드에서만 기본으로 여는 웹 콘솔 개발 서버(Vite) 출처.
DEV_WEB_ORIGIN = "http://localhost:5173"


def user_env_file(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get(CONFIG_DIR_ENV) or user_config_dir(APP_NAME, appauthor=False)
    return Path(base) / ".env"


def is_dev_mode(cwd: Path | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """저장소 체크아웃에서 개발 중인가. `AUTO_APPLY_DEV=1` 이거나 cwd 가 이 프로젝트 루트일 때만."""
    env = os.environ if environ is None else environ
    if env.get(DEV_MODE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    pyproject = (cwd or Path.cwd()) / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == APP_NAME


def env_files(
    cwd: Path | None = None, environ: Mapping[str, str] | None = None
) -> tuple[Path, ...]:
    """읽을 `.env` 목록 (뒤가 우선). 설치본은 사용자 설정 디렉터리 것만 읽는다.

    아무 폴더의 `./.env` 를 읽으면 설치본을 그 폴더에서 실행했다는 이유만으로 DRY_RUN_ONLY·DATA_DIR·
    API 키가 바뀐다(00-product 절대 규칙 2) — 그래서 `./.env` 는 개발 모드에서만 덧씌운다.
    """
    here = cwd or Path.cwd()
    files = [user_env_file(environ)]
    if is_dev_mode(here, environ):
        files.append(here / ".env")
    return tuple(files)


class Settings(BaseSettings):
    """환경변수 → 어댑터 선택 (bootstrap.py, §A2). `.env` 없이도 돈다(§A10).

    읽을 `.env` 는 프로세스 기동(import) 시점의 cwd·환경으로 정한다 — `env_files()` 참고.
    """

    model_config = SettingsConfigDict(env_file=env_files(), extra="ignore")

    app_env: Literal["local", "prod"] = "local"

    # 어댑터 선택
    llm_provider: Literal["stub", "anthropic", "claude_cli"] = "stub"
    storage: Literal["local", "memory"] = "local"
    resume_engine: Literal["simple"] = "simple"
    # 프로필·경험·답변KB·문서 메타도 이 repository 에 있다 (§A7) — 따로 고르는 소스가 없다.
    repository: Literal["sqlite", "memory"] = "sqlite"
    guide_source: Literal["static", "file"] = "file"

    # 저장소 (D3·D4: SQLite + 로컬 파일, §A1 데이터 디렉터리). 모든 경로는 여기서 파생한다 —
    # cwd 기준 경로를 두면 설치본을 어느 폴더에서 띄웠느냐에 따라 다른 데이터를 읽는다.
    data_dir: Path = DEFAULT_DATA_DIR

    # 외부 서비스
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # LLM_PROVIDER=claude_cli 일 때만 — API 키 대신 이 머신에 로그인된 Claude Code 구독을 쓴다.
    # `claude login`(또는 `claude setup-token`)이 이미 돼 있어야 한다.
    claude_cli_binary: str = "claude"
    claude_cli_model: str = "claude-sonnet-5"
    claude_cli_max_budget_usd: float = 0.5

    # 안전장치 (00-product 절대 규칙 2)
    dry_run_only: bool = True
    approval_timeout_hours: int = Field(default=72, ge=1)
    # 이 라운드를 넘으면 사람에게 넘긴다 — 무한 재생성 루프를 만들지 않는다
    max_revisions: int = Field(default=10, ge=1)

    # 이력서 블록 개수 안전 상한 (domain/resume_blocks.select_relevant_blocks). 몇 개를 보여줄지는
    # 가이드 + LLM 판단이 정하고, 이 값은 fact 가 비정상적으로 많을 때의 프롬프트 폭주 방지판이다.
    resume_max_project_blocks: int = Field(default=20, ge=1)
    resume_max_career_blocks_per_entity: int = Field(default=20, ge=1)

    # 웹 콘솔 개발 서버(Vite)의 origin 하나만 CORS 로 연다 — 계정 인증 없는 로컬 전용 서버라(D1)
    # 와일드카드를 쓰지 않는다. 변경 요청은 이 출처(또는 같은 출처)여야 한다 (§A10).
    # 기본값은 개발 모드에서만 — 설치본에서 localhost:5173 에 아무 앱이나 뜨면 그 앱이 토큰을
    # 받아 가게 되므로, 설치본은 같은 출처만 허용한다(명시적으로 지정하면 연다).
    web_cors_origin: str | None = Field(
        default_factory=lambda: DEV_WEB_ORIGIN if is_dev_mode() else None
    )

    # 업로드 문서(이력서·포트폴리오 원본) 한 개의 상한. 요청 본문은 이 값 + multipart 여유분까지만
    # 읽는다.
    document_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)

    @field_validator("web_cors_origin")
    @classmethod
    def _local_origin_only(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        parts = urlsplit(v)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in _LOCAL_HOSTS
            or parts.username is not None
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "WEB_CORS_ORIGIN 은 http(s)://127.0.0.1 · http(s)://localhost 출처만 허용한다"
            )
        _ = parts.port  # 포트가 숫자가 아니면 여기서 ValueError
        return v

    @property
    def dev_mode(self) -> bool:
        """§A10 개발 모드. API 문서(/docs·/redoc·/openapi.json)는 이때만 연다."""
        return is_dev_mode()

    @property
    def database_url(self) -> str:
        """DB 는 데이터 디렉터리 안의 파일 하나다 (§A1) — 따로 설정할 값이 아니라 파생값이다."""
        return f"sqlite+aiosqlite:///{(self.data_dir / 'db.sqlite3').resolve().as_posix()}"

    @property
    def files_dir(self) -> Path:
        """업로드·생성 파일 바이트(BlobStore 루트, §A1 `files/`)."""
        return self.data_dir / "files"

    @property
    def session_token_path(self) -> Path:
        """설치별 API 토큰 파일 (§A10). 최초 기동에 만들고 0600 으로 둔다."""
        return self.data_dir / "session_token"

    @property
    def guide_dir(self) -> Path:
        """`resume_guide.{platform}.md` 위치. §A8 가이드 DB(M6) 전까지의 파일 저장소."""
        return self.data_dir / "guides"


def load_settings() -> Settings:
    return Settings()
