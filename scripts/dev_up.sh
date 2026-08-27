#!/usr/bin/env bash
# 인프라(docker) + api/worker×3/telegram-listen/watchdog 를 한 번에, 누락 없이 기동한다.
# README "빠른 시작" 7단계를 사람이 터미널 여러 개에 나눠 치다가 리스너를 빼먹는 문제(2026-08-28) 때문에 존재한다.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
PID_DIR="$LOG_DIR/pids"
mkdir -p "$PID_DIR"

# name:command  (순서 = README 빠른 시작 4~7단계와 동일)
PROCS=(
  "worker-default:uv run python -m auto_apply.worker --queue default"
  "worker-ai:uv run python -m auto_apply.worker --queue ai"
  "worker-browser:uv run python -m auto_apply.worker --queue browser"
  "api:uv run uvicorn auto_apply.api.main:app --reload --port 8000"
  "telegram-listener:uv run python -m auto_apply.telegram.listener"
  "watchdog:uv run python -m auto_apply.watchdog"
)

FAILED=()
SKIPPED=()
STARTED=()

echo "── 1. 인프라 (docker compose up -d) ──"
if ! docker compose up -d; then
  echo "FATAL: docker compose up -d 실패 — 이후 단계를 진행하지 않는다." >&2
  exit 1
fi

if [ -f .env ] && grep -qE '^REPOSITORY=postgres' .env; then
  echo "── 2. 마이그레이션 (REPOSITORY=postgres) ──"
  # 인프라가 막 올라온 직후라 postgres 가 아직 연결을 안 받을 수 있다 — 몇 번 재시도.
  ok=0
  for i in 1 2 3 4 5; do
    if uv run alembic upgrade head; then
      ok=1
      break
    fi
    echo "  postgres 준비 대기 중... (${i}/5)"
    sleep 3
  done
  if [ "$ok" -ne 1 ]; then
    echo "FATAL: alembic upgrade head 5회 재시도 후에도 실패 — 이후 단계를 진행하지 않는다." >&2
    exit 1
  fi
else
  echo "── 2. 마이그레이션 스킵 (REPOSITORY != postgres) ──"
fi

echo "── 3. 앱 프로세스 기동 ──"
for entry in "${PROCS[@]}"; do
  name="${entry%%:*}"
  cmd="${entry#*:}"
  pid_file="$PID_DIR/$name.pid"
  log_file="$LOG_DIR/$name.log"

  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "  [$name] 이미 실행 중 (pid $(cat "$pid_file")) — 스킵"
    SKIPPED+=("$name")
    continue
  fi

  nohup bash -c "$cmd" >>"$log_file" 2>&1 &
  pid=$!
  echo "$pid" >"$pid_file"

  sleep 1.5
  if kill -0 "$pid" 2>/dev/null; then
    echo "  [$name] 기동 성공 (pid $pid) → $log_file"
    STARTED+=("$name")
  else
    echo "  [$name] 기동 직후 종료됨 — 로그 마지막 10줄:" >&2
    tail -n 10 "$log_file" 2>/dev/null | sed 's/^/      /' >&2
    rm -f "$pid_file"
    FAILED+=("$name")
  fi
done

echo ""
echo "── 요약 ──"
echo "성공: ${STARTED[*]:-없음}"
echo "이미 실행 중: ${SKIPPED[*]:-없음}"
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "실패: ${FAILED[*]}" >&2
  echo "" >&2
  echo "실패한 프로세스가 있다 — 위 로그를 보고 원인을 고친 뒤 scripts/dev_up.sh 를 다시 실행하면" >&2
  echo "이미 떠 있는 나머지는 건드리지 않고 실패했던 것만 다시 붙는다." >&2
  exit 1
fi

echo "모두 정상 기동. 상태 확인: scripts/dev_status.sh, 정지: scripts/dev_down.sh"
