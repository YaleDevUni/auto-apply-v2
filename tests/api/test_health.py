"""API 기동 스모크 — lifespan 이 데이터 디렉터리·마이그레이션·JobRunner 를 전부 세우는지 (§A1)."""

import sqlite3

from fastapi.testclient import TestClient

from auto_apply.api.main import create_app
from auto_apply.config import Settings

# 로컬 보안 미들웨어(§A10)가 Host 를 보므로 TestClient 기본 `testserver` 대신 루프백 주소로 부른다.
LOCAL = "http://127.0.0.1:8765"


def _settings(data_dir, **kw) -> Settings:
    return Settings(
        data_dir=data_dir,
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        **kw,
    )


def test_health_reports_dry_run_and_running_runner(tmp_path):
    app = create_app(_settings(tmp_path / "data", repository="memory"))
    with TestClient(app, base_url=LOCAL) as client:
        res = client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["dry_run_only"] is True
        assert body["runner"] == {"running": True}
        runner = app.state.container.runner
    assert runner.running is False


def test_startup_creates_data_dir_and_migrates_sqlite(tmp_path):
    data_dir = tmp_path / "first-run" / "auto-apply"
    app = create_app(_settings(data_dir, repository="sqlite"))
    with TestClient(app, base_url=LOCAL) as client:
        assert client.get("/health").status_code == 200

    db = data_dir / "db.sqlite3"
    assert db.is_file()
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"alembic_version", "applications", "runs"} <= tables


def test_restart_on_existing_db_is_noop(tmp_path):
    cfg = _settings(tmp_path / "data", repository="sqlite")
    for _ in range(2):
        with TestClient(create_app(cfg), base_url=LOCAL) as client:
            assert client.get("/health").status_code == 200
