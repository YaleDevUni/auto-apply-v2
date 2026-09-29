.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## 사용 가능한 명령
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup: ## 의존성 설치 + .env 생성
	uv sync
	@test -f .env || (cp .env.example .env && echo "→ .env 생성됨")

fmt: ## 포매팅
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint: ## 린트
	uv run ruff check src tests
	uv run ruff format --check src tests

type: ## 타입 체크
	uv run mypy

arch: ## 계층 규칙 검사 (§A2)
	uv run lint-imports

test: ## 테스트 — make check 가 쓰는 게이트 (docker·Chrome·claude CLI 불필요)
	uv run pytest -m "not native"

test-fast: ## 개발 중 빠른 반복용 — 첫 실패에서 멈추고 직전 실패부터 (게이트 아님)
	uv run pytest -m "not native" -x --ff

test-all: ## native 포함 전체 (Chrome·claude CLI 필요)
	uv run pytest

check: lint type arch test ## 커밋 전 전체 검사

api: ## FastAPI 개발 서버
	uv run uvicorn auto_apply.api.main:app --reload --port 8000

web: ## 웹 콘솔 dev 서버 (make api 가 먼저 떠 있어야 한다)
	cd web && npm install && npm run dev

.PHONY: help setup fmt lint type arch test test-fast test-all check api web
