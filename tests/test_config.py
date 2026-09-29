"""설정 파일 선택·값 검증 (§A10, 00-product 절대 규칙 2)."""

import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from auto_apply.config import Settings, env_files, is_dev_mode


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("DRY_RUN_ONLY", "LLM_PROVIDER", "AUTO_APPLY_DEV"):
        monkeypatch.delenv(key, raising=False)
    return {"AUTO_APPLY_CONFIG_DIR": str(tmp_path / "config")}


def test_unrelated_cwd_env_file_is_ignored(tmp_path, clean_env):
    """설치본을 아무 폴더에서 실행해도 그 폴더의 .env 가 dry_run 을 풀지 못한다."""
    work = tmp_path / "somewhere"
    _write(work / ".env", "DRY_RUN_ONLY=false\n")

    files = env_files(work, clean_env)

    assert work / ".env" not in files
    assert Settings(_env_file=files).dry_run_only is True


def test_dev_mode_env_var_reads_cwd_env_file(tmp_path, clean_env):
    work = tmp_path / "checkout"
    _write(work / ".env", "DRY_RUN_ONLY=false\n")

    files = env_files(work, {**clean_env, "AUTO_APPLY_DEV": "1"})

    assert files[-1] == work / ".env"
    assert Settings(_env_file=files).dry_run_only is False


@pytest.mark.parametrize(("name", "expected"), [("auto-apply", True), ("other-project", False)])
def test_dev_mode_detects_this_project_checkout(tmp_path, name, expected):
    _write(tmp_path / "pyproject.toml", f'[project]\nname = "{name}"\n')
    assert is_dev_mode(tmp_path, {}) is expected


def test_broken_pyproject_is_not_dev_mode(tmp_path):
    _write(tmp_path / "pyproject.toml", "[project\n")
    assert is_dev_mode(tmp_path, {}) is False


def test_user_config_dir_env_file_is_read(tmp_path, clean_env):
    _write(tmp_path / "config" / ".env", "LLM_PROVIDER=claude_cli\n")
    files = env_files(tmp_path / "somewhere", clean_env)
    assert Settings(_env_file=files).llm_provider == "claude_cli"


def _load_in(cwd, env) -> str:
    code = "from auto_apply.config import load_settings; print(load_settings().dry_run_only)"
    res = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def test_load_settings_end_to_end_ignores_foreign_env_file(tmp_path):
    """실제 프로세스 기동 경로(import 시점 cwd·환경)로 확인한다."""
    work = tmp_path / "downloads"
    _write(work / ".env", "DRY_RUN_ONLY=false\n")
    env = {k: v for k, v in os.environ.items() if k not in {"AUTO_APPLY_DEV", "DRY_RUN_ONLY"}}
    env["AUTO_APPLY_CONFIG_DIR"] = str(tmp_path / "config")

    assert _load_in(work, env) == "True"
    assert _load_in(work, {**env, "AUTO_APPLY_DEV": "1"}) == "False"


@pytest.mark.parametrize(
    "origin",
    ["*", "https://example.com", "http://localhost.evil.com", "http://localhost:5173/", "null"],
)
def test_cors_origin_rejects_non_local(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, web_cors_origin=origin)


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:8765"])
def test_cors_origin_accepts_local(origin):
    assert Settings(_env_file=None, web_cors_origin=origin).web_cors_origin == origin
