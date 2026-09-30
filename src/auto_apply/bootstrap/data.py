"""데이터 디렉터리 준비(마이그레이션)·설치별 토큰 — 기동 전에 끝나야 하는 것 (§A1·§A10)."""

import os
import secrets
from pathlib import Path

from auto_apply.adapters.repository.migrate import MigrationError, upgrade_to_head
from auto_apply.config import Settings


class StartupError(RuntimeError):
    """기동 준비 실패. 메시지는 사람이 읽을 한 줄(무엇이·어디서·왜) — 진입점이 그대로 보여준다."""


def prepare_data_dir(cfg: Settings) -> None:
    """첫 기동에도 그대로 뜨게: 데이터 디렉터리를 만들고 스키마를 head 로 올린다 (§A1).

    이미 head 면 Alembic 이 아무것도 하지 않으니 매 기동 호출해도 된다. 업그레이드는 원자적이라
    실패해도 스키마는 실패 전 그대로다(migrations/env.py).
    """
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StartupError(f"데이터 디렉터리를 만들 수 없다 — {cfg.data_dir}: {e}") from e
    if cfg.repository == "sqlite":
        try:
            upgrade_to_head(cfg.database_url)
        except MigrationError as e:
            raise StartupError(str(e)) from e


_TOKEN_MIN_LEN = 32


def ensure_session_token(path: Path) -> str:
    """설치별 랜덤 토큰을 읽고, 없거나 망가졌으면 새로 만든다 (§A10).

    파일은 소유자만 읽게(0600) 만든다. 이것만으로 다른 로컬 프로세스·같은 PC 의 다른 OS 계정을
    막지는 못한다 — 그쪽도 `GET /api/session` 으로 토큰을 얻을 수 있다(D1 위협 모델상 허용,
    M7 강화). 토큰이 막는 것은 브라우저가 대신 보내는 타 사이트 요청(CSRF)이다.
    Windows 는 POSIX 권한 비트가 없다.
    """
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if len(token) >= _TOKEN_MIN_LEN:
        if os.name != "nt":
            path.chmod(0o600)
        return token
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 새 파일은 처음부터 0600 으로 연다 — 쓰고 나서 chmod 하면 그 사이에 넓은 권한으로 보인다.
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    tmp.replace(path)
    return token
