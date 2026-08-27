#!/usr/bin/env bash
# scripts/dev_up.sh 가 관리하는 프로세스 + 인프라 컨테이너 상태를 한눈에 보여준다.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_DIR="$ROOT_DIR/logs/pids"
EXPECTED=(worker-default worker-ai worker-browser api telegram-listener watchdog)

echo "── 앱 프로세스 ──"
for name in "${EXPECTED[@]}"; do
  pid_file="$PID_DIR/$name.pid"
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    printf "  %-18s RUNNING (pid %s)\n" "$name" "$(cat "$pid_file")"
  else
    printf "  %-18s DOWN\n" "$name"
  fi
done

echo ""
echo "── 인프라 (docker compose) ──"
docker compose ps
