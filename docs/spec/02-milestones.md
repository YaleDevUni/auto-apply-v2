# 02 · 마일스톤 · 태스크 카드

> 태스크 카드는 **서브에이전트 한 번에 끝낼 수 있는 크기**로 자른다 (파일 범위 명시, 수용 기준은 테스트로).
> 상세 카드는 **마일스톤 시작 시점에** 쓴다 — 미리 써둔 세부는 앞 마일스톤 결과로 낡는다.
> 지금은 M0 만 상세, M1~M7 은 목표·수용 기준까지만 있다.

카드 형식:
```
### T{M}.{n} 제목
- 의존: T…            - 범위: 건드릴 경로 (이 밖은 건드리지 않는다)
- 할 일: …
- 수용 기준: (테스트/명령으로 확인 가능한 것만)
```

---

## M0 · 정리와 뼈대 (v2 → v3 전환)

목표: 폐기 대상 코드를 걷어내고, Temporal/Postgres 없이 `make check` 가 녹색인 최소 뼈대.

### T0.1 폐기 코드 삭제
- 의존: 없음
- 범위: `src/auto_apply/`, `tests/`, `scripts/`, `config/`, `Makefile`, `docker-compose.yml`, `db-init/`, `var/`
- 할 일: 삭제 — `telegram/`, 공고 수집(`job_source`, `job_collection`, `matching_config`, `domain/job_*`), Recipe 전부
  (`recipe*`, `executor/` 의 RecipeExecutor 구현, `agent_browser_selector`, repair 워크플로우/activity/프롬프트),
  플랫폼 어댑터(`platform/`, `attachments/`, `_wanted_auth`, `_saramin_auth`, `login_flow`), 스케줄
  (`schedule*.py`, `schedule_cron`), `watchdog.py`, `process_alerts.py`, `pending_decisions.py`, `resume_cleanup.py`,
  `guide_patch`, `adapters/notifier/telegram.py`, `api/routers/{recipes,telegram}.py`, `scripts/` 중 recipe/wanted/saramin 전용,
  대응 테스트 전부. `apply_intake.py` 는 삭제(트리거는 M4 에서 서비스로 재작성).
- **과감하게 지운다** (사용자 지시 2026-09-29: v2 는 legacy 브랜치에 있으니 v3 와 안 맞는 건 "참고용"으로도 남기지 않는다).
  추가 삭제: `adapters/web_agent/`(Aside, 동결분), `adapters/credentials/`, `adapters/checkpoint/`·CheckpointWaiter,
  `adapters/executor/` 전체, `adapters/pdf/weasyprint.py`(§A7 에서 Chrome PDF 로 교체 — port+stub 만 남김),
  `adapters/notifier/`(console 포함 — v3 는 웹 UI 가 알림), `domain/chat_agent.py`, `domain/alerting.py`(텔레그램 전제면),
  `scripts/` 의 v2 전용 전부(explore_platform·auto_login·save_auth_state·guards·dev_*.sh), `var/`·`config/` 의 v2 데이터,
  `.env.example` 의 v2 키.
- **보존**(v3 가 그대로 재사용하는 것만): Fact·`resume_matching`·`resume_blocks`·`ground_check`, 이력서 불릿 생성 로직,
  `adapters/llm/*`(ClaudeCodeCliLLM·anthropic·stub), `adapters/storage/local`·memory, `web/`(M1 에서 재구성).
- 수용 기준: `make lint type arch` 통과. 남은 테스트 통과.
  `grep -ril "telegram\|recipe\|wanted\|saramin\|jasoseol" src/ tests/ scripts/` 결과 0.

### T0.2 Temporal 제거 · SQLite 전환
- 의존: T0.1
- 범위: `src/auto_apply/{workflows,activities,contracts/activity_defs.py,worker.py,temporal_config.py,bootstrap.py,config.py}`,
  `adapters/repository/`, `alembic/`, `pyproject.toml`, `Makefile`, `docker-compose.yml`
- 할 일: workflows/activities/worker/temporal 의존성 제거(이력서 생성 로직은 `services/document.py` 로 옮겨 살림).
  Postgres·S3·MinIO 어댑터 제거, SQLite 단일 repository(+ memory 대역) + Alembic 초기 리비전 재작성
  (§A3·§A7·§A8·§A9 테이블은 이후 마일스톤에서 추가 — 여기선 applications/runs 최소 스키마만). `docker-compose.yml` 삭제.
  `make up/migrate/worker/dev-*` 제거, `make check` 는 docker·temporal 없이 돌게.
  T0.1 검증에서 넘어온 잔재도 여기서: `pyproject` 의 python-telegram-bot·weasyprint·selectolax·boto3(+mypy override,
  native 마커 설명, description 의 "recipe-driven", 삭제된 모듈명 주석), `config.resume_engine` 의 구현 없는 `langgraph` 값,
  `adapters/repository/models.py` 의 삭제된 `persist_state` 참조. playwright 는 §A5/§A7 용도로 유지.
- 수용 기준: `make check` 녹색, `pyproject` 에 temporalio/asyncpg/boto 없음, repository contract test 가 sqlite+memory 둘 다에 돈다.

### T0.3 계층 규칙 · 앱 진입점 재구성
- 의존: T0.2
- 범위: `src/auto_apply/{services,runner,api,bootstrap.py,__main__.py}`, arch 검사 스크립트, `pyproject.toml`
- 할 일: §A2 계층(domain/contracts/ports/adapters/services/runner/api/bootstrap)으로 arch 규칙 갱신. `auto-apply` 콘솔 스크립트 →
  platformdirs 데이터 디렉터리 생성 + 마이그레이션 자동 적용 + FastAPI(127.0.0.1) + 빈 JobRunner 기동. `/health`.
- 수용 기준: `uv run auto-apply --port 0` 기동 스모크 테스트 통과, arch 검사가 새 계층 위반을 잡는 테스트 1개.

### T0.4 문서 정리
- 의존: T0.3
- 범위: `docs/`, `README.md`, `CLAUDE.md`
- 할 일: `docs/ARCHITECTURE.md`·`docs/architecture/`·`docs/RUNBOOK.md` 삭제(legacy 브랜치에 있음), README 를 v3 개요로 교체
  (ASCII 다이어그램, 정중체), CLAUDE.md 의 명령어 절을 실제 Makefile 과 맞춤.
- 수용 기준: 문서 내 링크 깨짐 0 (`make docs-check` 또는 간단한 링크 검사 스크립트).

---

## M1 · 프로필 · 지식베이스
목표: 웹에서 인적사항/경험/문서/답변KB 를 CRUD 하고, 기존 이력서 파일로 초안을 추출한다.
웹 콘솔은 **TanStack Router(D16)** 로 페이지 라우팅을 도입하고, T0.1 에서 끊긴 `web/src/lib/api.ts` 의 `/applications/*` 호출을 걷어낸다.
수용 기준: 프로필 API contract 테스트, 추출 결과 스키마 검증 회귀 테스트(샘플 PDF 2종: 개발/비개발), 웹 온보딩 화면 수동 확인 체크리스트.

## M2 · 브라우저 호스트 · 제출 차단 하네스
목표: 설치된 Chrome 을 전용 프로필로 띄우고(mac/win 경로 탐지), BrowserToolbox 도구와 §A4 L1~L5 를 구현.
수용 기준: **테스트 짐 전 픽스처에서 FILL 단계 제출 0건**, 클릭 분류기 단위 테스트, 로그인 핸드오프 픽스처 테스트. (`native` 마커)

## M3 · 에이전트 런타임 · 채우기(fill) run
목표: AgentRuntime 3구현, MCP(HTTP) 노출, fill run 이 픽스처 사이트에서 FillLog + `ready_for_review` 까지.
수용 기준: Scripted 런타임으로 상태기계 전 경로 테스트, CLI 런타임은 `native` 마커 e2e 1건(짐 사이트), ask_user 일시정지/재개 테스트.

## M4 · 승인 큐 · 재진입(submit/revise)
목표: 트리거 API/UI, 승인 큐 UI(스크린샷·필드표·편집), submit run(§A4 L6 대조), revise run, 크래시 복구.
수용 기준: SUBMIT_MISMATCH 경로 테스트, dry_run 에서 클릭 0회 검증, 중복 지원 경고 테스트.

## M5 · 공고맞춤 문서 · 직군 템플릿
목표: 4개 직군 이력서 템플릿 + 포트폴리오(직군별 필요도), Chrome PDF 렌더, 자소서 답변 생성(ground_check·글자수).
수용 기준: 직군별 렌더 골든 테스트(HTML 스냅샷), ground_check 회귀 테스트, 글자수 초과 거부 테스트.

## M6 · 가이드 자동 학습
목표: run 종료 반성 → 가이드 자동 버전 누적, 압축, UI(목록·diff·롤백·편집), 프롬프트 주입.
v2 잔재인 `ports/guide.py`·`adapters/guide/file.py`·`config.py` 의 `resume_guide.{platform}.md` 키는 D13(도메인별+전역)으로 교체한다.
수용 기준: 반성 결과 스키마 검증 테스트, 롤백 후 주입 내용 검증, "가이드로 하네스 해제 불가" 회귀 테스트.

## M7 · 배포
목표: `uv tool install` 한 줄 설치, 웹 빌드 산출물 패키지 포함, 첫 실행 마법사, mac/windows CI 매트릭스, README 설치 가이드.
수용 기준: CI 가 mac·windows 에서 `make check` 녹색, 깨끗한 환경 설치 스모크.
