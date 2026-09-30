# 02 · 마일스톤 · 태스크 카드

> 태스크 카드는 **서브에이전트 한 번에 끝낼 수 있는 크기**로 자른다 (파일 범위 명시, 수용 기준은 테스트로).
> 상세 카드는 **마일스톤 시작 시점에** 쓴다 — 미리 써둔 세부는 앞 마일스톤 결과로 낡는다.
> M0·M1(완료)·M2 는 상세, M3~M7 은 목표·수용 기준까지만 있다.

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
수용 기준: **테스트 짐 전 픽스처에서 FILL 단계 제출 0건**(D17 받아들인 위험 `next_final_nosignal` 만 1건 뒤 L5 INCIDENT), 클릭 분류기 단위 테스트, 로그인 핸드오프 픽스처 테스트. (`native` 마커)
순서: T2.1 · T2.2 · T2.3 → T2.4 → T2.5 → T2.6 → T2.7(D17, 사용자 결정 2026-09-30).
공통: 브라우저가 필요한 테스트는 `native` 마커. CI·Chrome 없는 환경을 위해 테스트는 Playwright 번들 Chromium 으로도 돌 수 있게
(`AUTO_APPLY_TEST_BROWSER=chromium`), 제품 기본은 설치된 Chrome(D6).

### T2.1 BrowserHost — 설치 Chrome 탐지 · 전용 프로필 · 수명주기
- 의존: M1
- 범위: `src/auto_apply/{ports/browser.py,adapters/browser/,domain/errors.py,config.py,bootstrap.py,api/main.py}`, `tests/`, `pyproject.toml`
- 할 일: `BrowserHost` port(벤더 타입 노출 금지 — 페이지 핸들은 불투명 타입). 실제 구현: Playwright 로 **설치된 Chrome**을
  `DATA_DIR/chrome-profile` 전용 user-data-dir 로 headful 기동(`launch_persistent_context(channel="chrome")` 우선, 실패 시
  mac/Windows 표준 설치 경로 탐지 → `executable_path`). 사용자 기본 프로필은 절대 쓰지 않는다(D6). 프로필 디렉터리 단일 인스턴스
  잠금(두 번째 기동은 명확한 오류), 지연 기동(첫 사용 시)·앱 종료 시 정리, 브라우저를 사람이 닫아도 다음 사용 시 재기동.
  테스트 대역(메모리 페이지 모델) + contract test. Chrome 미설치는 `ChromeNotFound`(첫 실행 마법사는 M7).
- 수용 기준: 경로 탐지 단위 테스트(mac/Windows 경로를 가짜 파일시스템으로), contract test(실제=native + 대역),
  native: 전용 프로필로 기동 → 쿠키 저장 → 재기동 후 쿠키 유지, 동시 두 번째 기동 거부.

### T2.2 제출 클릭 분류기 (§A4 L2)
- 의존: M1
- 범위: `src/auto_apply/{domain/submit_classifier.py,contracts/}`, `tests/domain/`
- 할 일: 순수 함수 `classify_click(ElementDescriptor) -> Safe | Risky(reason)`. 기술자 = tag·type·role·접근 이름·텍스트·
  form 소속/기본 버튼 여부·dialog 안 여부·href. Risky: `type=submit`, 폼 기본 버튼, 제출 어휘(한·영: 제출/지원하기/지원 완료/최종/
  Submit/Apply/Send/Finish/Confirm…), dialog 안의 확인/OK/예. **판단이 애매하면 Risky**(닫힌 쪽으로 실패). 어휘는 데이터로 분리.
  완료 어휘(§A4 L5: "지원이 완료", "application received"…) 판정 함수도 여기.
- 수용 기준: 픽스처 표 회귀 테스트(최소 40케이스, 한·영, 전각·공백 변형, "다음"/"저장 후 계속"/"파일 선택" 은 Safe), 완료 어휘 테스트.

### T2.3 테스트 짐 — 로컬 픽스처 사이트 · 제출 기록 서버
- 의존: M1
- 범위: `tests/fixtures/sites/`, `tests/gym/`(pytest 픽스처·서버), `pyproject.toml`(테스트 의존성만)
- 할 일: §A4 목록의 정적 사이트를 만든다 — SPA fetch 제출, multipart form 제출, confirm 대화상자 뒤 제출, "지원하기"가 폼을
  여는 버튼, 다단계(중간 저장 POST 는 허용돼야 함), iframe 안 폼, 어휘 없는 "확인" 제출 버튼, sendBeacon 제출, 클릭 핸들러의
  `form.requestSubmit()`, 제출 후 완료 페이지("지원이 완료되었습니다"), 로그인 벽 페이지(쿠키 없으면 로그인 폼으로 리다이렉트).
  127.0.0.1 임의 포트 서버가 **최종 제출 엔드포인트 수신을 기록**해 테스트가 "제출 0건"을 단언할 수 있게. 사이트별 기대값
  (최종 제출 요청, 허용되는 중간 요청)을 매니페스트로.
- 수용 기준: 서버·매니페스트 단위 테스트, native: 각 사이트를 Playwright 로 **하네스 없이** 직접 조작하면 제출이 기록된다
  (= 픽스처가 진짜 제출 경로를 가진다는 양성 대조).

### T2.4 BrowserToolbox 기본 도구 · FillLog (§A5, §A4 L1)
- 의존: T2.1
- 범위: `src/auto_apply/{contracts/fill_log.py,contracts/,ports/,adapters/browser/,services/browser_toolbox*.py,domain/}`, `tests/`
- 할 일: 도구를 **한 곳**에서 정의(런타임 중립, M3 가 CLI/API 로 노출): `snapshot()`(접근성 트리 + ref, 비밀번호 필드 값 가림),
  `navigate`·`back`·`scroll`·`wait_for`, `fill`/`select`/`check`(`source` 필수 → FillLog 자동 기록), `upload(ref, document_id)`
  (앱이 관리하는 문서만 — 임의 경로 불가), `report_failure`. L1: 임의 JS·키 입력(Enter)·좌표 클릭 도구는 **존재하지 않는다**.
  `click` 은 T2.5 에서 하네스와 함께 붙이므로 여기선 정의하지 않는다. 도구 입력은 Pydantic 검증(ref 모양, source 종류).
- 수용 기준: 도구 스키마 테스트(금지 도구 부재 단언 포함), FillLog 기록 테스트, native: 픽스처 폼에서 snapshot→fill→FillLog,
  비밀번호 필드 값이 snapshot 에 안 나오는 테스트, 등록 안 된 파일 upload 거부.

### T2.5 SubmitGuard — click · L3 격리 · L4 대화상자 · L5 사후 감지 · ready_for_review
- 의존: T2.2, T2.3, T2.4
- 범위: `src/auto_apply/{adapters/browser/,services/browser_toolbox*.py,services/submit_guard*.py,domain/,contracts/}`, `tests/`
- 할 일: `click(ref)` = 분류(T2.2) → Safe 는 relaxed(단계 저장·업로드 POST 허용), Risky 는 **strict 창**: 클릭 전후 창 동안
  비-GET(document/xhr/fetch/beacon) 전부 abort + init script 로 `form.submit/requestSubmit`·submit 이벤트 차단(iframe 포함).
  차단 발생 → `SUBMIT_BLOCKED` 반환. L4: FILL 단계 `confirm()/beforeunload` 자동 거절. L5: 클릭 후 URL/본문 완료 어휘 →
  run 즉시 중단 + `INCIDENT` 이벤트. `ready_for_review(submit_ref, notes)`: 제출 대상 기술자(선택자 후보·텍스트·위치) + FillLog 확정.
  **하네스 설정은 가이드·프롬프트·도구 인자로 바꿀 수 없다**(모드 전환 API 없음). 실패하면 막는 쪽으로 닫힌다(라우팅 설치 실패 = 클릭 거부).
- T2.2 이관(설계 과제): 분류기는 허용 목록 방식이라 `type=submit` 인 "다음"(단계별 폼 POST 저장)도 Risky → strict 가
  중간 저장을 막는다. 차단을 약화하지 말고, 차단된 클릭이 **최종 제출인지 단계 이동인지 모르는 상태**를 에이전트에 그대로 알려
  (`SUBMIT_BLOCKED` 에 "단계 이동일 수 있음" 구분 없이 ready_for_review 로 유도하되 FillLog 에 단계 표시), 다단계 POST 사이트는
  승인 후 SUBMITTING 에서 하네스가 단계별로 진행하는 방식(§A4 L6 확장)을 §A4 에 설계로 적는다. 짐에 type=submit 다단계 픽스처 포함.
  `detect_completion` 은 클릭 전 페이지 문구와 비교해 원래 있던 문구로 오탐하지 않게.
- T2.3 이관(설계 과제): L3 는 비-GET·form submit 만 막아 **GET 탐색으로 제출**(`location.href="/apply?…"`)하는 사이트는 L5 사후 감지뿐.
  Risky 클릭 창에서의 문서 탐색 처리 규칙을 §A4 에 정하고(예: 쿼리/본문을 싣는 GET 탐색은 차단, 단순 페이지 이동은 허용 — "지원하기"가 폼 페이지로
  이동하는 픽스처는 통과해야 함), 짐에 GET 제출 픽스처를 추가해 0건 단언. 짐 헬퍼는 `tests/gym/fixtures.py`(gym·gym_browser,
  `GymServer.final_submissions()`·`intermediate_requests()`·`wait_for()`). native 가 느리면(Chrome 기준 ~28초) module 스코프 검토.
- T2.4 이관: 사이트가 스스로 **앱 출처(127.0.0.1:앱포트·localhost 별칭)로 리다이렉트/요청**하는 경우는 navigate 검사로 못 막음 →
  브라우저 context 수준 route 로 앱 출처 요청 차단. `check` 가 체크박스를 클릭하므로 onchange 핸들러 발신도 relaxed/strict 창 정책에 포함.
  한 줄 입력칸의 줄바꿈을 Chrome 이 지워 FillLog 값과 DOM 값이 다를 수 있음 → 입력 정규화 규칙을 정해 FillLog 에 정규화 값 기록(L6 대조 대비).
- 수용 기준: native **짐 전 픽스처에서 FILL 단계 제출 0건** — 모든 버튼을 차례로 누르는 적대적 스크립트로(T2.3 기록 서버 단언),
  다단계 중간 저장은 통과, 완료 페이지 도달 시 INCIDENT, confirm 거절 테스트, 하네스 층을 하나씩 끄면 테스트가 실패하는지 확인.

### T2.6 로그인 벽 감지 · 사람 핸드오프
- 의존: T2.3, T2.4
- 범위: `src/auto_apply/{ports/human_gate.py,adapters/human_gate/,services/browser_toolbox*.py,domain/,contracts/}`, `tests/`
- 할 일: `request_login(site)`·`request_human(reason)` 도구 + `HumanGate` port(실행이 사람 응답을 기다리는 통로 — asyncio Future,
  최대 대기 설정). 구현 2개: 인메모리(테스트·단일 프로세스) — UI 연결은 M3/M4. 로그인 벽 휴리스틱(비밀번호 입력 필드·로그인 URL
  패턴)은 domain 순수 함수, 도구는 **비밀번호를 입력하지 않는다**(절대 규칙 3 — fill 이 password 타입 필드를 거부). CAPTCHA 감지 시
  `request_human`. 타임아웃이면 `NEEDS_LOGIN`/`NEEDS_INPUT` 결과로 종료(상태 전이는 M3/M4 가 ApplicationService 로).
- T2.1 이관: 전용 프로필 Chrome 에서 Google 계정 로그인이 자동화 플래그(`--enable-automation`)로 막히는지 실측(사람 로그인 흐름에 치명적이면
  플래그 조정은 **우회가 아니라** 정상 브라우저로 보이게 하는 범위에서만 — CAPTCHA·봇 탐지 우회 금지). BrowserHost 탭 조작을 port 로 올릴지 결정(T2.4).
- T2.5 이관: 사람 로그인 대기 중에는 가드 disarm(사람의 로그인·SSO 폼 제출을 막지 않게) 후 재개 시 re-arm. 가드가 꺼진 동안에도
  dialog 처리기가 confirm 을 자동 거절한다 — 사람 핸드오프 중엔 사람에게 넘기도록. 파일 선택 버튼의 OS 대화상자(filechooser) 가로채기.
- 수용 기준: native 로그인 벽 픽스처 — 감지 → 대기 → (테스트가 사람 대신 쿠키 설정) → 재개 → 폼 도달. password 필드 fill 거부 테스트,
  타임아웃 테스트, 로그인 휴리스틱 단위 테스트.

### T2.7 단계 이동 자동 통과 (D17) — 최종 제출만 승인
- 의존: T2.5, T2.6
- 범위: `src/auto_apply/{domain/submit_classifier.py,domain/submit_vocabulary.py,domain/submit_guard_policy.py,services/submit_guard*.py,
  services/browser_toolbox*.py,adapters/browser/,contracts/}`, `tests/`, `tests/fixtures/sites/`, `tests/gym/`, `docs/spec/01-architecture.md` §A4
- 할 일: D17 구현. 분류 결과에 **STEP**(단계 이동) 추가 — `type=submit` 이라도 라벨이 명확한 단계 어휘(데이터로)면 STEP, 단 같은 페이지에
  **마지막 단계 신호**(진행 표시가 마지막 단계, 편집 가능한 입력칸 없는 검토/요약 페이지, 최종 제출 동의·"제출 전 확인" 문구)가 있으면 Risky.
  STEP 클릭은 폼 POST(단계 이동)를 허용하되 **사후 확인**: 결과 화면에 완료 어휘 → INCIDENT(L5), 새 입력 화면 → 계속,
  입력칸도 완료 근거도 없는 화면 → run 멈춤 + `request_human`(애매하면 닫힌 쪽). 제출 어휘·애매한 라벨은 지금처럼 strict.
  §A4 "다단계 사이트" 절을 D17 로 다시 쓰고(단계마다 승인하던 L6 확장 설계 제거 — 승인은 최종 제출 1회), 남는 위험 갱신.
  dry_run 에서도 STEP 은 누른다(최종 제출만 안 함).
- 수용 기준: 분류기 회귀 테스트(STEP/Risky 경계 — 마지막 단계 신호별), 짐에 픽스처 추가: 마지막 버튼이 "다음"이면서 최종 제출하는 사이트
  (**마지막 단계 신호 있음 → FILL 제출 0건**, 신호 없음 → INCIDENT 로 즉시 중단되는지), 검토 페이지형, type=submit 3단계 사이트가
  승인 없이 마지막 단계까지 도달하고 최종 버튼에서 SUBMIT_BLOCKED. 기존 짐 전 픽스처 FILL 제출 0건 유지(적대 스크립트).

## M3 · 에이전트 런타임 · 채우기(fill) run
목표: AgentRuntime 3구현, MCP(HTTP) 노출, fill run 이 픽스처 사이트에서 FillLog + `ready_for_review` 까지.
수용 기준: Scripted 런타임으로 상태기계 전 경로 테스트, CLI 런타임은 `native` 마커 e2e 1건(짐 사이트), ask_user 일시정지/재개 테스트.
순서: T3.1 → T3.2 → T3.3 → T3.4 → T3.5 → T3.6 → T3.7 (병렬 금지 중이라 직렬). 카드는 에이전트 1회(~15만 토큰) 크기로 잘랐다.
공통: 실제 외부 사이트 접속 금지(짐 픽스처만). `claude` CLI·Chrome 이 필요한 테스트는 `native`.

### T3.1 v3 상태기계 · ApplicationService.transition (§A3)
- 의존: 없음(M2 완료)
- 범위: `src/auto_apply/{domain/,contracts/,ports/repository.py,adapters/repository/,services/application.py}`, `tests/`, §A3
- 할 일: `domain/enums.ApplicationState` 를 v2 값(COLLECTING·EVALUATING·SCHEDULED·EXECUTING…)에서 §A3 v3 값으로 교체하고 전이 표를
  `domain/application_state.py` 순수 함수로(허용 안 된 전이는 `InvalidTransition`). **INCIDENT 상태 추가**(하네스 L5 가 승인 없는 제출
  가능성을 감지 — 사람이 확인해 SUBMITTED 또는 CANCELLED 로 닫는다, 자동 재시도 없음)를 §A3 그림·표에 반영.
  `ApplicationService.transition(application_id, to, *, run_id, reason)` 을 상태 쓰기 **유일 통로**로 — 저장소 쓰기 메서드는 서비스만
  부르도록 arch 계약 또는 테스트로 봉인(절대 규칙 6). Alembic `0003`(0002 는 T1.1 프로필이 씀): applications 에 `url`·`domain`·`submit_mode`, `runs` 에
  `kind`·`status(RUNNING|DONE|FAILED|INTERRUPTED)`·`result`·토큰·transcript 경로. v2 잔재 정리: M4 이관이던 `upsert_state`→`append_state`,
  `PersistState.workflow_run_id`→`run_id` 를 여기서.
- 수용 기준: 전이 표 전 경로(허용·금지) 표 테스트, 이력 append-only 유지, 서비스 밖에서 상태 쓰기 시도가 실패하는 테스트,
  마이그레이션 up/down·models 대조 테스트, 기존 데이터(M1 이후 applications 0행 가정)로 업그레이드 통과.

### T3.2 JobRunner 큐 소비 · 재시도 정책 · 크래시 복구 (§A9)
- 의존: T3.1
- 범위: `src/auto_apply/{runner/,adapters/repository/,ports/,domain/errors.py,domain/application_state.py,services/application.py,bootstrap.py}`, `tests/`, §A9·§A3
- 할 일: Alembic `0004` `jobs` 테이블(§A9 컬럼). JobRunner 가 jobs 를 소비 — 브라우저 작업(fill/revise/submit)은 동시성 1, 그 외는 별도 슬롯.
  핸들러 등록 표(kind → handler), 핸들러가 없는 kind 는 FAILED. 재시도 정책을 `domain/errors.py` 에서 §A9 기준으로 재정의
  (T0.2 이관 `NON_RETRYABLE` Temporal 근거 제거): 인프라성 실패만 지수 백오프·상한, 도메인 실패(로그인·입력 필요·하네스 차단·INCIDENT)는
  재시도 없이 상태 전이. 기동 시 크래시 복구: `RUNNING` run·job → `INTERRUPTED`, 지원 건은 직전 재개 가능 상태로(§A3).
  종료 시 진행 중 job 은 취소하고 INTERRUPTED 로. T3.1 이관: run 저장소 메서드(시작·종료·INTERRUPTED 복구), ApplicationService·JobRunner bootstrap 배선.
  **크래시 복구 SUBMITTING→FAILED 를 →INCIDENT 로 바꾼다**(최종 클릭이 나갔는지 모름 — FAILED 는 '제출됐을 수 있음'을 숨긴다. 사람이 SUBMITTED/CANCELLED 로 닫음), 전이 표·§A3 같이.
- 수용 기준: 동시성 1 보장 테스트(브라우저 job 2개 동시 투입), 재시도/비재시도 분류 표 테스트, 크래시 복구 테스트(행을 RUNNING 으로
  심고 기동), 정지 시 대기 중 job 이 남아 다음 기동에 소비됨.

### T3.3 AgentRuntime port · ScriptedAgentRuntime · fill run 핸들러 (§A6)
- 의존: T3.2
- 범위: `src/auto_apply/{ports/agent.py,ports/jobs.py,adapters/agent/scripted.py,ai/,runner/,services/,contracts/,domain/errors.py,bootstrap.py}`, `tests/`, §A6
- 할 일: `AgentRuntime` port — `run(system_prompt, tools: 도구 명세 목록, call_tool, *, limits) -> AgentOutcome`(벤더 타입 노출 금지,
  도구 실행은 호출자가 넘긴 `call_tool` 로만 — 런타임이 브라우저를 직접 만지지 않는다). `ScriptedAgentRuntime`(미리 적은 도구 호출
  시퀀스 재생) + contract test 틀(이후 CLI·API 구현이 params 로 붙는다). fill run 핸들러: 시스템 프롬프트 조립(역할·규칙 + 전역/도메인
  가이드 자리 + fill 지시 + 프로필 요약, `ai/` 순수 빌더) → BrowserToolbox 로 도구 연결 → 결과를 상태 전이로:
  `ready_for_review`→AWAITING_APPROVAL(ReviewRecord·FillLog 저장), `needs_human` NEEDS_LOGIN/NEEDS_INPUT, `report_failure`·도구 한도 초과→FAILED,
  L5 INCIDENT→INCIDENT, 가드 불가(GUARD_UNAVAILABLE)→FAILED(재시도 없음). run 한도(도구 호출 수·시간) 설정.
  에이전트 출력 텍스트는 상태에 영향 없음 — 오직 도구 결과만.
  T3.2 이관: fill 핸들러를 bootstrap handlers 에 등록·run 저장소 start/finish 호출, 도구 결과 INCIDENT 는 `SubmitIncident`/직접 전이.
  job·run DTO 를 `ports/jobs.py` 에서 `contracts/` 로 옮기고(§A2 — DTO 는 contracts), `domain/errors.py`(227줄) 책임 분리 검토.
- 수용 기준: Scripted 런타임 + 짐 픽스처(native)로 fill run → AWAITING_APPROVAL, 제출 0건. 대역 드라이버로 모든 종료 경로 → 상태 표 테스트.
  에이전트가 없는 도구 이름·잘못된 인자를 부르면 ToolResult 에러로 돌려받고 run 은 계속(한도까지).

### T3.4 ask_user · 답변 KB · 재진입 run (D8, D10)
- 의존: T3.3
- 범위: `src/auto_apply/{services/browser_toolbox*.py,contracts/,ports/human_gate.py,adapters/human_gate/,runner/fill.py,services/profile.py}`, `tests/`, §A5
- 할 일: `ask_user(question, field_hint, options?, sensitive?)` 도구 — HumanGate 로 대기(T2.6 과 같은 통로, `HumanTask.kind=QUESTION`).
  답은 `sensitive=false` 면 답변 KB 에 저장(D10, 질문 키 정규화 `domain/question_key.py`), `sensitive=true`·주민번호 꼴이면 저장 안 함(절대 규칙 5).
  대기 타임아웃 → NEEDS_INPUT 으로 종료, 답이 오면 **재진입 run**: 같은 URL 로 다시 열고 직전 run 의 FillLog 부분 기록 + 받은 답을 프롬프트에
  넣어 이어간다(값 재입력은 에이전트가 하되 FillLog 에 새로 쌓임). T2.6 이관: `human_wait_s` 설정 키.
- 수용 기준: ask_user 일시정지 → 답 → 재개 테스트(대역), 타임아웃 → NEEDS_INPUT → 답 → 재진입 run → AWAITING_APPROVAL(Scripted·짐 native),
  sensitive 답이 DB·로그·transcript 어디에도 없음을 단언.

### T3.5 BrowserToolbox MCP(HTTP) 노출 (§A5)
- 의존: T3.3
- 범위: `src/auto_apply/{api/mcp*.py,services/,runner/}`, `pyproject.toml`(mcp SDK), `tests/`, §A5·§A10
- 할 일: run 마다 발급하는 **run 토큰**으로만 열리는 MCP streamable HTTP 엔드포인트(127.0.0.1, 앱과 같은 프로세스). 도구 목록은
  `browser_toolbox_specs.TOOLS` 그대로(여기서 도구를 새로 정의하지 않는다), 호출은 그 run 의 `BrowserToolbox.call`. run 이 끝나면 토큰 폐기.
  Host·Origin 검사는 기존 API 와 같게. MCP 에 설치 토큰(`session_token`)은 통하지 않고, run 토큰으로 REST API 는 안 열린다(교차 거부).
- 수용 기준: MCP 클라이언트(SDK)로 list_tools = TOOLS 이름 집합, 토큰 없음·만료·다른 run 토큰 거부, run 토큰으로 `/api/*` 거부,
  도구 호출이 같은 FillLog 에 쌓임.

### T3.6 ClaudeCliAgentRuntime (기본, D5)
- 의존: T3.5
- 범위: `src/auto_apply/{adapters/agent/claude_cli*.py,adapters/llm/,config.py,bootstrap.py}`, `tests/`, §A6
- 할 일: `claude -p --output-format stream-json --mcp-config <run별 파일> --strict-mcp-config`, **내장 도구 전부 비활성**(Bash·Read·WebFetch 등 —
  에이전트가 승인 API 를 부르거나 파일을 읽는 통로 차단), 우리 MCP 도구만 allow. 작업 디렉터리는 `DATA_DIR/runs/<run_id>/`(저장소의 CLAUDE.md·
  사용자 설정 훅이 끼지 않게 설정 원천 격리). v2 `ClaudeCodeCliLLM` 의 프로세스 관리·장애 시그니처(로그인 풀림/한도초과) 재사용.
  transcript 는 run 디렉터리에(고유식별정보 가림). 프로세스 종료·타임아웃·취소 시 자식 프로세스 정리. `config.llm_provider` 기본을
  `claude_cli` 로(D5 — 테스트·오프라인 게이트는 stub 유지), 모델 기본값 최신화.
- 수용 기준: stream-json init 이벤트의 도구 목록이 **우리 MCP 도구뿐**임을 단언(native), 짐 사이트 e2e 1건 → AWAITING_APPROVAL·제출 0건(native),
  한도초과·로그인 풀림 시그니처 → 인프라 실패 분류(대역 stdout), 취소 시 잔여 프로세스 0.

### T3.7 AnthropicApiAgentRuntime · bootstrap 배선
- 의존: T3.6
- 범위: `src/auto_apply/{adapters/agent/anthropic*.py,bootstrap.py,config.py,runner/}`, `tests/`, §A6·§A10
- 할 일: Messages API tool-use 루프(같은 TOOLS 를 in-process 로), 가짜 HTTP 전송으로 테스트. AgentRuntime contract test 에 CLI·API params 추가.
  bootstrap: BrowserHost 와 짝지은 PageDriver·SubmitGuard·BrowserToolbox 조립(T2.4 이관), `forbidden_origins` 에 앱 자신의 주소(실제 포트)
  주입, HumanGate 주입(T2.6 이관), JobRunner 에 fill 핸들러 등록, 런타임 선택(`llm_provider`).
- 수용 기준: API 런타임 contract test(가짜 전송), 앱 기동 → fill job 투입 → Scripted 런타임으로 AWAITING_APPROVAL 까지 통합 테스트,
  forbidden_origins 에 실제 포트가 들어감을 단언.

M3 이후 실사용 점검(사용자와, 카드 아님): T2.5 "남는 위험"(안전 라벨 fetch 최종 제출·WebSocket·3초 넘게 미룬 제출·shadow DOM submit·
가드 이름 선점), T2.7 단계 어휘·마지막 단계 신호 오탐·미탐, shadow DOM snapshot 누락, T2.6 Google 로그인 webdriver 차단 여부 —
실사이트 dry_run fill 로그로 빈도를 보고 추가 층·어휘 조정을 M4 이후 카드로 만든다.

## M4 · 승인 큐 · 재진입(submit/revise)
목표: 트리거 API/UI, 승인 큐 UI(스크린샷·필드표·편집), submit run(§A4 L6 대조), revise run, 크래시 복구.
(상태 쓰기 봉인·`append_state`·`run_id` 개명은 T3.1 로 옮김.)
T2.6 이관: HumanGate `pending()`·`answer()` 를 승인 큐 UI 에 연결, NEEDS_LOGIN/NEEDS_INPUT 전이는 ApplicationService 로.
T2.7 이관: 단계 이동 후 도착 화면이 UNCLEAR 일 때 하네스가 여는 사람 넘김도 같은 UI 로. L6 값 대조는 FillLog 중 `step == ReviewRecord.step` 칸만 DOM 재독.
dry_run→live 전환은 settings 테이블 + UI 확인으로만(설정 파일로 조용히 뒤집히지 않게, 절대 규칙 2).
수용 기준: SUBMIT_MISMATCH 경로 테스트, dry_run 에서 **최종 제출** 클릭 0회 검증(D17 — 단계 이동 클릭은 dry_run 에서도 함), 중복 지원 경고 테스트.

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
