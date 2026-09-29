"""API 스모크 — v2 라우터를 걷어낸 뒤에도 앱이 뜨고 컨테이너가 조립되는지."""

from fastapi.testclient import TestClient

from auto_apply.api.main import app


def test_healthz_reports_dry_run(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE", "memory")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("REPOSITORY", "memory")
    monkeypatch.setenv("DRY_RUN_ONLY", "true")
    with TestClient(app) as client:
        res = client.get("/healthz")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["dry_run_only"] is True
