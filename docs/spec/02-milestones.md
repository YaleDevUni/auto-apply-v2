# 02 · 마일스톤 · 태스크 카드

> 태스크 카드는 **서브에이전트 한 번에 끝낼 수 있는 크기**로 자른다 (파일 범위 명시, 수용 기준은 테스트로).
> 상세 카드는 **마일스톤 시작 시점에** 쓴다 — 미리 써둔 세부는 앞 마일스톤 결과로 낡는다.
> M0·M1(완료)은 상세, M2~M7 은 목표·수용 기준까지만 있다.

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
- T0.2 검증 이관: Alembic 스크립트를 패키지 안(`src/auto_apply/…`)으로 옮겨 설치본에서도 자동 마이그레이션이 되게. `data_dir`·`env_file` 을 platformdirs 로.
  남은 v2 주석(`domain/errors.py` 의 Temporal 근거 제외) 정리.

### T0.4 문서 정리
- 의존: T0.3
- 범위: `docs/`, `README.md`, `CLAUDE.md`
- 할 일: `docs/ARCHITECTURE.md`·`docs/architecture/`·`docs/RUNBOOK.md` 삭제(legacy 브랜치에 있음), README 를 v3 개요로 교체
  (ASCII 다이어그램, 정중체), CLAUDE.md 의 명령어 절을 실제 Makefile 과 맞춤.
- 수용 기준: 문서 내 링크 깨짐 0 (`make docs-check` 또는 간단한 링크 검사 스크립트).

---

## M1 · 프로필 · 지식베이스
목표: 웹에서 인적사항/경험/문서/답변KB 를 CRUD 하고, 기존 이력서 파일로 초안을 추출한다.
수용 기준: 프로필 API contract 테스트, 추출 결과 스키마 검증 회귀 테스트(샘플 PDF 2종: 개발/비개발), 웹 온보딩 화면 수동 확인 체크리스트.
병렬: T1.3(백엔드)과 T1.4(웹)는 파일 범위가 겹치지 않아 T1.2 뒤 worktree 로 병렬 실행 가능.

### T1.1 프로필 저장소 DB 전환 (Profile · Experience · AnswerKB · Document)
- 의존: M0
- 범위: `src/auto_apply/{contracts,ports,domain,adapters/{repository,profile,facts,storage},config.py,bootstrap.py}`,
  `adapters/repository/migrations/versions/0002_*`, `tests/`
- 할 일: §A7 모델을 SQLite 로. **Profile** 을 기본(이름·연락처·링크·학력·스킬·언어)과 **추가 정보**(병역·보훈·장애·희망연봉·
  입사가능일·거주지역 — 전부 선택, 비어 있으면 실행 중 `ask_user`, D10)로 나눈 언어 중립 필드로 확장.
  **Experience**(v2 Fact 일반화: entity 종류 company/project/activity/education, 기간·역할·fact 문장·`skills`·링크·첨부 문서 id).
  **AnswerKB**(정규화 질문 키, 답, 출처 application_id, 갱신일). **Document**(업로드 고정 파일 메타 — 바이트는 BlobStore `files/`).
  고유식별정보 거부는 도메인 규칙으로(주민등록번호 패턴을 Profile·AnswerKB 어느 필드에도 저장 불가, 절대 규칙 5).
  yaml/static 소스(`adapters/{facts,profile}`)는 repository(sqlite+memory)로 교체하고 `config.py` 의 `./config/...` 경로 제거.
  `LocalBlobStore` 루트를 `DATA_DIR/files/` 로. 기존 이력서 생성(DocumentService)이 새 저장소로 계속 동작해야 한다.
- 수용 기준: repository contract test 가 sqlite·memory 둘 다에 돈다(프로필·경험·답변·문서 CRUD), 주민번호 거부 테스트,
  0002 마이그레이션 up/down + 모델 대조 테스트, `grep -rn "\./config" src/` 0건, 기존 DocumentService 테스트 녹색.

### T1.2 ProfileService · 프로필 REST API · 로컬 보안 헤더
- 의존: T1.1
- 범위: `src/auto_apply/{services/profile.py,api/,bootstrap.py,config.py}`, `tests/`
- 할 일: ProfileService(검증·주민번호 거부·AnswerKB 질문 키 정규화). `/api/profile`, `/api/experiences`, `/api/answers`,
  `/api/documents`(multipart 업로드: pdf/docx/png/jpg 화이트리스트·크기 상한) CRUD. §A10 로컬 보안을 **첫 변경 API 와 함께** 도입:
  설치별 랜덤 토큰(데이터 디렉터리 파일 0600, 최초 기동 생성) + 모든 변경 요청에 `Origin` 검사 + 토큰 헤더,
  웹이 토큰을 받는 `GET /api/session`(CORS 로 타 출처 차단). 에러는 일관된 JSON 스키마.
- 수용 기준: API contract 테스트(성공·검증 실패·404), 토큰 없음/틀린 Origin → 403 테스트, 업로드 타입·크기 거부 테스트.
- T1.1 이관: Experience/fact id 발급과 사용자 단위 유일성은 서비스가 보장(저장소는 강제 안 함), 기본 `user_id` 상수 1곳 정의.

### T1.3 이력서 파일 → 프로필 초안 추출 (+ v2 yaml 가져오기)
- 의존: T1.2
- 범위: `src/auto_apply/{ports,adapters/extract/,ai/,services/,api/}`, `tests/fixtures/resumes/`, `pyproject.toml`
- 할 일: 새 외부 의존성 절차(§A2)대로 `DocumentTextExtractor` port(PDF·DOCX → 텍스트) + 구현 2개(실제 라이브러리 + 테스트 대역).
  `ai/` 에 추출 프롬프트·구조화 출력 스키마(Profile/Experience 초안). LLM 출력은 Pydantic 검증 통과분만 **초안**으로 저장,
  사용자가 확정해야 본 프로필에 병합(`POST /api/profile/drafts/{id}/confirm`, 항목별 선택). v2 `config/facts.yaml`·`profile.yaml`
  을 같은 초안 경로로 가져오는 임포터. 픽스처 PDF 2종(개발/비개발)은 **가상의 인물**로 만든다(실제 개인정보 금지).
- 수용 기준: 추출 스키마 검증 회귀 테스트(픽스처 2종 × stub LLM 고정 응답, 잘못된 응답 거부), 초안→확정 병합 테스트,
  yaml 임포터 테스트, 추출기 contract test(실제+대역).
- T1.1 이관: `config/*.example.yaml` 은 임포터 입력 양식으로만 남기거나 픽스처로 옮기고 `config/` 정리.

### T1.4 웹 콘솔 재구성 — TanStack Router · 레이아웃 · 인적사항 화면
- 의존: T1.2
- 범위: `web/`
- 할 일: TanStack Router(D16) 도입, v2 화면(`ApplicationList`·`ApplicationDetail`·`ApplyBar` 등)과 `api.ts` 의 `/applications/*`
  제거. API 클라이언트를 `/api/session` 토큰 헤더 포함으로 재작성, TanStack Query 훅. 공통 레이아웃(사이드 내비: 프로필·경험·답변·문서).
  **인적사항 화면**: 기본 항목은 펼침, 추가 정보(병역·보훈·장애·희망연봉·입사가능일·거주지역)는 접힌 섹션 +
  "비워두면 지원 중에 물어봅니다" 안내. i18n 키 구조(D14, 리소스는 ko 만).
- 수용 기준: `npm run build`·`npm run lint` 통과, `docs/spec/checklists/m1-web.md` 에 인적사항 화면 수동 확인 항목 작성.

### T1.5 웹 — 경험 · 답변KB · 문서 · 온보딩(추출 검토) 화면
- 의존: T1.3, T1.4
- 범위: `web/`, `docs/spec/checklists/m1-web.md`, `Makefile`
- 할 일: 경험 목록/편집(entity 별 그룹), 답변KB 목록/편집/삭제, 문서 업로드·목록. 첫 화면 온보딩: 이력서 파일 업로드 →
  추출 초안 항목별 검토(채택/수정/버림) → 확정. 프로필이 비어 있으면 온보딩으로 유도.
  T1.3 이관: 온보딩 업로드는 `POST /api/profile/drafts/upload`(문서로 저장 안 함, 주민번호는 LLM 전에 가려지고
  초안에 `redacted_identifiers` 개수만 — "N개를 가렸습니다" 안내), 이어서 검토는 `GET /api/profile/drafts`. 문서 업로드(`/api/documents`)는
  pdf/docx 본문에 주민번호가 있으면 422 — 전용 안내 문구.
  T1.4 이관: `make check` 에 웹 게이트(`npm run build`·`lint`·`test`) 포함 — node 없는 환경이면 건너뛰되 경고.
  `src/routes/placeholders.tsx` 를 실제 화면으로 교체, 끝까지 안 쓴 shadcn ui(card·separator·textarea) 삭제.
- 수용 기준: `npm run build`·`npm run lint` 통과, 체크리스트에 온보딩 흐름 추가, 오케스트레이터가 로컬 기동 후 체크리스트 수동 확인.

## M2 · 브라우저 호스트 · 제출 차단 하네스
목표: 설치된 Chrome 을 전용 프로필로 띄우고(mac/win 경로 탐지), BrowserToolbox 도구와 §A4 L1~L5 를 구현.
수용 기준: **테스트 짐 전 픽스처에서 FILL 단계 제출 0건**, 클릭 분류기 단위 테스트, 로그인 핸드오프 픽스처 테스트. (`native` 마커)

## M3 · 에이전트 런타임 · 채우기(fill) run
목표: AgentRuntime 3구현, MCP(HTTP) 노출, fill run 이 픽스처 사이트에서 FillLog + `ready_for_review` 까지.
이관: `domain/errors.py` 의 `NON_RETRYABLE`(Temporal 근거)을 §A9 JobRunner 재시도 정책으로 재정의하거나 삭제.
`config.llm_provider` 기본값이 `stub` — D5(기본 Claude Code CLI)에 맞추되 테스트·오프라인 게이트는 stub 유지.
수용 기준: Scripted 런타임으로 상태기계 전 경로 테스트, CLI 런타임은 `native` 마커 e2e 1건(짐 사이트), ask_user 일시정지/재개 테스트.

## M4 · 승인 큐 · 재진입(submit/revise)
목표: 트리거 API/UI, 승인 큐 UI(스크린샷·필드표·편집), submit run(§A4 L6 대조), revise run, 크래시 복구.
이관: 상태 쓰기 포트를 `ApplicationService` 만 쓰도록 봉인(절대 규칙 6), `upsert_state`→`append_state` 개명(이미 append-only),
`PersistState.workflow_run_id`→`run_id`.
dry_run→live 전환은 settings 테이블 + UI 확인으로만(설정 파일로 조용히 뒤집히지 않게, 절대 규칙 2).
수용 기준: SUBMIT_MISMATCH 경로 테스트, dry_run 에서 클릭 0회 검증, 중복 지원 경고 테스트.

## M5 · 공고맞춤 문서 · 직군 템플릿
목표: 4개 직군 이력서 템플릿 + 포트폴리오(직군별 필요도), Chrome PDF 렌더, 자소서 답변 생성(ground_check·글자수).
이관: `ResumeReviewExhausted` 를 `services/document.py` 에서 `domain/errors.py` 로.
수용 기준: 직군별 렌더 골든 테스트(HTML 스냅샷), ground_check 회귀 테스트, 글자수 초과 거부 테스트.

## M6 · 가이드 자동 학습
목표: run 종료 반성 → 가이드 자동 버전 누적, 압축, UI(목록·diff·롤백·편집), 프롬프트 주입.
v2 잔재인 `ports/guide.py`·`adapters/guide/file.py`·`config.py` 의 `resume_guide.{platform}.md` 키는 D13(도메인별+전역)으로 교체한다.
수용 기준: 반성 결과 스키마 검증 테스트, 롤백 후 주입 내용 검증, "가이드로 하네스 해제 불가" 회귀 테스트.

## M7 · 배포
목표: `uv tool install` 한 줄 설치, 웹 빌드 산출물 패키지 포함, 첫 실행 마법사, mac/windows CI 매트릭스, README 설치 가이드.
이관: Makefile `SHELL := /bin/bash` 등 Windows 비호환 정리(개발 명령을 Python 태스크로).
T0.3 이관: DB 업그레이드 전 자동 백업, `make api`(uvicorn CLI)가 `UVICORN_HOST`/`--host` 로 0.0.0.0 에 열리는 개발 경로 봉인,
`api/main.py` 모듈 수준 `app` 제거(import 만으로 Settings 이중 로드).
T1.4 이관: 빌드된 웹을 FastAPI 가 같은 출처로 서빙(SPA history 경로 → index.html 폴백).
T1.2 이관(로컬 보안 강화): 토큰을 CLI 가 여는 일회성 URL(fragment) → HttpOnly·SameSite=Strict 쿠키로 전달하고 `/api/session` 제거,
GET 에도 토큰 요구(지금은 같은 PC 의 로컬 프로세스·다른 OS 계정이 토큰·인적사항을 얻을 수 있음), OpenAPI 422 에러 스키마 정정,
db.sqlite3·업로드 파일 권한 0600(Windows ACL 포함).
수용 기준: CI 가 mac·windows 에서 `make check` 녹색, 깨끗한 환경 설치 스모크.
