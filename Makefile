.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## 사용 가능한 명령
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup: ## 의존성 설치 + .env 생성
	uv sync
	@test -f .env || (cp .env.example .env && echo "→ .env 생성됨")
	@test -f config/facts.yaml || (cp config/facts.example.yaml config/facts.yaml && echo "→ config/facts.yaml 생성됨 (실제 이력으로 채울 것)")

up: ## 인프라 기동 (postgres/temporal/temporal-ui/minio)
	docker compose up -d
	@echo "Temporal UI → http://localhost:8080   MinIO → http://localhost:9001"

down: ## 인프라 정지
	docker compose down

reset: ## 인프라 + 볼륨 삭제 (데이터 날아감)
	docker compose down -v

migrate: ## Alembic 마이그레이션 적용 (REPOSITORY=postgres 일 때, make up 필요)
	uv run alembic upgrade head

fmt: ## 포매팅
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint: ## 린트
	uv run ruff check src tests
	uv run ruff format --check src tests

type: ## 타입 체크
	uv run mypy

arch: ## 계층 규칙 검사 (ARCHITECTURE.md §11.7)
	uv run lint-imports

test: ## 단위/계약/워크플로우 테스트 (docker·브라우저 불필요) — make check 가 쓰는 게이트
	uv run pytest -m "not docker and not native"

test-fast: ## 개발 중 빠른 반복용 — Temporal 을 띄우는 테스트까지 뺀다 (게이트 아님)
	uv run pytest -m "not docker and not native and not temporal"

test-all: ## 전체 (make up + playwright/weasyprint 설치 필요)
	uv run pytest

check: lint type arch test ## 커밋 전 전체 검사

api: ## FastAPI 개발 서버
	uv run uvicorn auto_apply.api.main:app --reload --port 8000

worker: ## Temporal worker (QUEUE=default|ai|browser)
	uv run python -m auto_apply.worker --queue $${QUEUE:-default}

telegram-listen: ## 텔레그램 롱폴링 리스너 (NOTIFIER=telegram, 공인 URL 없는 로컬 개발용)
	uv run python -m auto_apply.telegram.listener

watchdog: ## 워크플로우 능동 감시 (FAILED/TERMINATED/TIMED_OUT → 알림, WATCHDOG_* 로 튜닝)
	uv run python -m auto_apply.watchdog

resume-cleanup: ## wanted 이력서 첨부파일 정리 (기본 dry-run, ARGS="--yes" 로 실제 삭제)
	uv run python -m auto_apply.resume_cleanup $(ARGS)

web: ## 웹 콘솔 dev 서버 (§12, make api 가 먼저 떠 있어야 한다)
	cd web && npm install && npm run dev

.PHONY: help setup up down reset migrate fmt lint type arch test test-fast test-all check api worker telegram-listen watchdog resume-cleanup web
