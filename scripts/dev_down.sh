#!/usr/bin/env bash
# scripts/dev_up.sh 로 띄운 앱 프로세스를 정지한다. --infra 를 주면 docker compose down 도 같이 한다.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_DIR="$ROOT_DIR/logs/pids"

if [ ! -d "$PID_DIR" ]; then
  echo "정지할 프로세스 없음 ($PID_DIR 없음)"
else
  shopt -s nullglob
  for pid_file in "$PID_DIR"/*.pid; do
    name="$(basename "$pid_file" .pid)"
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "  [$name] SIGTERM 전송 (pid $pid)"
    else
      echo "  [$name] 이미 죽어 있음 (pid $pid)"
    fi
    rm -f "$pid_file"
  done
  shopt -u nullglob
fi

if [ "${1:-}" = "--infra" ]; then
  echo "── 인프라 정지 (docker compose down) ──"
  docker compose down
fi
