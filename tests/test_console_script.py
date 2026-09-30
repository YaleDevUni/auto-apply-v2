"""`auto-apply --port 0` 기동 스모크 — 설치된 콘솔 스크립트가 빈 데이터 디렉터리에서 뜨고 /health 에
응답하고 신호로 깔끔하게 내려가는지 (§A1). 실제 설치본과 같은 경로(콘솔 스크립트)를 탄다.
"""

import os
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import httpx

READY = re.compile(r"auto-apply ready: (http://127\.0\.0\.1:(\d+))")


def _console_script() -> Path:
    name = "auto-apply.exe" if os.name == "nt" else "auto-apply"
    return Path(sys.executable).parent / name


def _pump(stream, lines: "queue.Queue[str]") -> None:
    for line in stream:
        lines.put(line)


def _isolated_env(tmp_path: Path, data_dir: Path) -> dict[str, str]:
    """dotenv 보다 환경변수가 우선한다 — 개발자 .env 가 무엇이든 오프라인 조합으로 띄운다.
    사용자 설정 디렉터리(.env)도 tmp 로 돌리고, 개발 모드 스위치는 끈다(cwd 도 저장소 밖)."""
    env = {k: v for k, v in os.environ.items() if k != "AUTO_APPLY_DEV"}
    env.update(
        {
            "AUTO_APPLY_CONFIG_DIR": str(tmp_path / "config"),
            "DATA_DIR": str(data_dir),
            "LLM_PROVIDER": "stub",
            "STORAGE": "local",
            "REPOSITORY": "sqlite",
            "FACTS_SOURCE": "static",
            "PROFILE_SOURCE": "static",
            "GUIDE_SOURCE": "static",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def test_console_script_boots_and_serves_health(tmp_path):
    script = _console_script()
    assert script.is_file(), f"{script} 없음 — `uv sync` 로 콘솔 스크립트를 설치할 것"

    data_dir = tmp_path / "data"
    env = _isolated_env(tmp_path, data_dir)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(script), "--port", "0"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        creationflags=flags,
    )
    lines: queue.Queue[str] = queue.Queue()
    pump = threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)
    pump.start()
    seen: list[str] = []
    try:
        url = None
        while url is None:
            line = lines.get(timeout=60)
            seen.append(line)
            if m := READY.search(line):
                url = m.group(1)
        assert int(m.group(2)) != 0

        res = httpx.get(f"{url}/health", timeout=10)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["runner"] == {"running": True}
        # 고른 포트가 앱 안까지 간다 — 금지 출처·MCP URL 이 이 값에서 나온다 (§A5·§A6)
        assert body["server_origin"] == url
        assert Path(body["data_dir"]) == data_dir
        assert (data_dir / "db.sqlite3").is_file()
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    pump.join(timeout=5)
    while not lines.empty():
        seen.append(lines.get())
    out = "".join(seen)
    # Windows 의 CTRL_BREAK 는 콘솔 종료 신호라 종료 코드가 0 이 아니다 — "내려갔다"까지만 본다.
    if os.name != "nt":
        assert proc.returncode == 0, out
        assert "job_runner.stopped" in out, out


def test_console_script_reports_migration_failure_in_one_line(tmp_path):
    """마이그레이션이 실패하면 트레이스백이 아니라 DB 경로·원인 한 줄을 남기고 1 로 끝난다."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "db.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("create table runs (id integer primary key)")

    res = subprocess.run(
        [str(_console_script()), "--port", "0"],
        cwd=tmp_path,
        env=_isolated_env(tmp_path, data_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "Traceback" not in out
    assert "DB 마이그레이션 실패" in out
    assert str(db.resolve()) in out
    assert "already exists" in out
