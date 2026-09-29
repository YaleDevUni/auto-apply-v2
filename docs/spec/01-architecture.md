# 01 · 아키텍처 (v3)

> 절 번호(§A*)는 코드 주석·테스트가 참조한다 — 내용을 바꿔도 번호는 유지한다.
> 결정 근거는 [00-product.md](00-product.md) 의 D-번호.

## §A1 구성도

```
┌──────────── 사용자 PC (mac / Windows) ─────────────────────────────────────┐
│                                                                            │
│  auto-apply (단일 Python 프로세스, uv)                                       │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────────────┐  │
│  │ FastAPI      │   │ JobRunner     │   │ BrowserHost                  │  │
│  │  REST + 정적  │──▶│ (asyncio,     │──▶│  Playwright ─ CDP ─▶ Chrome  │  │
│  │  웹(React)   │   │  DB 큐 소비)   │   │  전용 프로필 (headful)        │  │
│  │  MCP(HTTP)   │   └──────┬────────┘   │  + SubmitGuard (§A4)         │  │
│  └──────┬───────┘          │            └──────────────▲───────────────┘  │
│         │                  ▼                           │ BrowserToolbox    │
│         │           ┌─────────────┐   tool calls       │ (§A5)             │
│         │           │AgentRuntime │────────────────────┘                   │
│         │           │ CLI | API   │── claude -p (구독) / Anthropic API       │
│         │           └─────────────┘                                        │
│         ▼                                                                  │
│   SQLite + 파일 저장소 (platformdirs 데이터 디렉터리)                          │
└────────────────────────────────────────────────────────────────────────────┘
```

- 프로세스는 **하나**: API, 작업 큐 러너, 브라우저 호스트, MCP 엔드포인트가 한 프로세스에 산다.
  브라우저를 소유한 프로세스가 하네스(§A4)도 소유해야 우회 경로가 생기지 않는다.
- 데이터 디렉터리: `platformdirs.user_data_dir("auto-apply", appauthor=False)` — `db.sqlite3`, `files/`, `chrome-profile/`, `runs/`.
  `DATA_DIR` 로 덮어쓸 수 있다(테스트·개발).
- 진입점: `auto-apply [--port N]`(콘솔 스크립트, `__main__.py`). 기동 순서는 FastAPI lifespan 하나에 모여 있어
  `uvicorn auto_apply.api.main:app` 으로 띄워도 같다 — ① 데이터 디렉터리 생성 ② Alembic head 자동 적용
  ③ 컨테이너 조립 ④ JobRunner 기동. 종료 시 JobRunner 를 먼저 세운다. 바인드는 `127.0.0.1` 고정(옵션 없음),
  `--port 0` 이면 빈 포트를 골라 `auto-apply ready: http://127.0.0.1:<port>` 한 줄을 찍는다. 상태 확인은 `GET /health`.

## §A2 계층 (make arch 가 강제)

```
domain      순수 로직: 상태기계, ground_check, 필드 대조, 제출 버튼 분류 규칙, 가이드 병합
contracts   pydantic DTO (extra=forbid, frozen). 벤더 SDK 금지
ai          LLM 프롬프트 빌더 + 구조화 출력 스키마(순수 Pydantic). 벤더 SDK·port·어댑터 금지 — adapters·services 가 가져다 쓴다
ports       Protocol. 벤더 타입 노출 금지
adapters    port 구현 (llm/, agent/, browser/, pdf/, storage/, repository/)
services    유스케이스 (ApplicationService, ProfileService, GuideService, DocumentService) — port 만 안다
runner      JobRunner + run 핸들러 (fill/revise/submit/generate)
api         라우터 (컨테이너에서 서비스 꺼내 씀)
bootstrap   ★ 어댑터를 생성하는 유일한 파일
```

`make arch` = import-linter(`pyproject.toml` `[tool.importlinter]`). 의존 방향은
`domain·contracts < ai·ports < services < runner < bootstrap < api < __main__`, adapters 는 bootstrap 만 안다.
규칙이 실제로 위반을 잡는지는 `tests/test_arch.py` 가 위반을 심은 복사본으로 확인한다.

새 외부 의존성 추가 절차(Protocol → 예외 계약 → **구현 2개(실제+테스트 대역)** → contract test → bootstrap)는 v2 규칙을 그대로 유지한다.

## §A3 지원 상태기계

```
          trigger
DRAFT ───────────▶ QUEUED ──▶ FILLING ──┬──▶ AWAITING_APPROVAL ──approve──▶ SUBMITTING ──▶ SUBMITTED
                                 ▲  │    │        │   │                           │
                  answer/login ──┘  ▼    │   revise   reject                       ├──▶ SUBMIT_MISMATCH ─▶ AWAITING_APPROVAL
                          NEEDS_INPUT    │        ▼   ▼                           │    (재입력 값이 기록과 다름)
                          NEEDS_LOGIN    │   REVISING  REJECTED                    └──▶ FAILED
                                         └──▶ FAILED        │
                                                            └──▶ AWAITING_APPROVAL
어느 상태에서든 cancel ──▶ CANCELLED
```

- 전이 표는 `domain/application_state.py` 에 순수 함수로. 허용되지 않은 전이는 예외.
- 상태를 쓰는 통로는 `ApplicationService.transition()` **하나** (v2 의 persist_state 규칙 계승).
- `runs` 테이블: 에이전트 세션 1회 = run 1행 (`kind=fill|revise|submit`, 토큰, 소요시간, transcript 경로, 결과).
- 초기 스키마(T0.2, Alembic `0001`): `applications`(지원 건 1행 = 최신 상태 스냅샷) · `application_state_history`
  (전이 이력 = 감사 로그, **append-only** — 전이마다 새 행, 자동 증가 `id` 가 순번, 최신 = 가장 큰 순번. 같은 run 안의
  FILLING↔NEEDS_INPUT 왕복도 그대로 쌓인다) · `runs`(최소 컬럼). 스키마는 Alembic 이 유일한 원천이고
  `models.py` 와의 일치는 테스트가 대조한다. 리비전 스크립트는 설치본에도 실리도록 패키지 안
  (`adapters/repository/migrations/`)에 있고 `adapters/repository/migrate.py` 가 ini 없이 Config 를 조립한다
  (루트 `alembic.ini` 는 `alembic revision` 개발용).
- 크래시 복구: 기동 시 `RUNNING` run 을 `INTERRUPTED` 로 닫고 지원 건을 직전 재개 가능 상태로 되돌린다.

## §A4 제출 차단 하네스 (SubmitGuard) — 제품의 핵심 안전장치

원칙: **LLM 을 신뢰하지 않는다. 차단은 결정적 코드이고, 여러 겹이며, 실패하면 막는 쪽으로 닫힌다.**

| 층 | 무엇을 | 어떻게 |
|---|---|---|
| L1 도구 표면 | 에이전트가 쓸 수 있는 동작 제한 | 임의 JS 실행·Enter 키·좌표 클릭 도구 없음. CLI 경로는 우리 MCP 도구만 허용(`--strict-mcp-config`, 내장 도구 전부 차단) |
| L2 클릭 분류 | 모든 `click(ref)` 전에 대상 분류 | `domain/submit_classifier.py`: `type=submit`, 폼 기본 버튼, 제출 어휘(제출/지원하기/지원 완료/최종/Submit/Apply/Send…), dialog 안의 확인/OK/예 → **risky**. 순수 함수, 픽스처로 회귀 테스트 |
| L3 risky 클릭 격리 | risky 클릭은 **strict 네트워크 모드**에서만 실행 | 클릭 창(window) 동안 비-GET 요청(document/xhr/fetch/beacon) 전부 abort + 페이지 내 `form.submit/requestSubmit`·submit 이벤트 차단. 차단이 발생하면 "이 클릭은 제출 동작이었다"로 판정해 에이전트에게 `SUBMIT_BLOCKED` 반환 → `ready_for_review` 로 유도 |
| L4 네이티브 대화상자 | `confirm()/beforeunload` | FILL 단계에선 자동 dismiss(거절) |
| L5 사후 감지 | 뚫렸는지 확인 | 클릭 후 URL/본문에 완료 어휘("지원이 완료", "application received"…)가 나타나면 run 즉시 중단 + `INCIDENT` 이벤트 + UI 경고 |
| L6 제출 실행 | 승인 후에만 | SUBMITTING 단계에서도 에이전트는 제출 버튼을 못 누른다. `commit_submit()` 을 부르면 **하네스가** (a) FillLog 필드 값 DOM 재판독·대조 (b) 기록된 제출 대상과 동일 요소인지 확인 (c) `submit_mode=live` 확인 후 직접 클릭. dry_run 이면 클릭 없이 증거만 남김 |

- 비-risky 클릭(다음/Next/저장 후 계속, 파일 업로드 등)은 relaxed 모드 — 단계 저장·업로드 POST 허용.
- **테스트 짐(gym)**: `tests/fixtures/sites/` 에 로컬 정적 사이트 — SPA fetch 제출, multipart 제출, confirm 대화상자 제출,
  "지원하기"가 폼 여는 버튼인 경우, 다단계 저장, iframe 폼, 제출 어휘가 없는 버튼(`확인`) 등.
  **모든 픽스처에서 FILL 단계 제출 성공 0건**이 하네스 PR 의 통과 조건이다.

## §A5 BrowserToolbox (에이전트 도구)

한 곳에서 정의하고 두 런타임이 공유한다: API 경로는 in-process tool, CLI 경로는 같은 함수를 MCP(HTTP, 127.0.0.1, run 토큰)로 노출.

| 도구 | 설명 |
|---|---|
| `snapshot()` | 접근성 트리 + ref (agent-browser 스타일). 비밀번호 필드 값은 가림 |
| `navigate(url)` · `back()` · `scroll(ref?)` · `wait_for(text|ms)` | 이동 |
| `click(ref)` | §A4 L2/L3 경유 |
| `fill(ref, value, source)` · `select(ref, option, source)` · `check(ref, on, source)` | `source` = 근거(profile 필드 / fact_id / answer_kb / generated / user). FillLog 에 자동 기록 |
| `upload(ref, document_id)` | 앱이 관리하는 파일만 (임의 경로 금지) |
| `generate_document(kind, …)` | 공고맞춤 이력서/포트폴리오 PDF, 자소서 문항 답변 — DocumentService 호출(§A7) |
| `ask_user(question, field_hint, options?, sensitive?)` | 실행 일시정지 → UI 질문. `sensitive=true` 면 답변 KB 에 저장 안 함 |
| `request_login(site)` · `request_human(reason)` | 로그인 벽·CAPTCHA → 사람 핸드오프 후 재개 |
| `ready_for_review(submit_ref, notes)` | FILL 종료. 제출 대상 요소 기술자(선택자 후보·텍스트·위치)와 FillLog 확정 |
| `report_failure(reason)` | 진행 불가 |
| `commit_submit()` | **SUBMITTING run 에서만 노출.** 실제 클릭은 하네스가 (§A4 L6) |

- `ask_user`/`request_login` 은 도구 호출이 UI 응답(asyncio Future)을 **최대 N분**(설정) 기다린다. 넘기면 run 을 `NEEDS_INPUT` 으로
  종료하고, 답이 오면 **재진입 run** 이 FillLog 부분 기록부터 이어간다 (D8 과 같은 메커니즘).

## §A6 AgentRuntime (port) — 구현 3개

| 구현 | 용도 |
|---|---|
| `ClaudeCliAgentRuntime` (기본, D5) | `claude -p --output-format stream-json --mcp-config <run별 설정> --strict-mcp-config`, 내장 도구 전부 비활성, 우리 MCP 도구만 allow. v2 `ClaudeCodeCliLLM` 의 프로세스 관리·장애 시그니처(로그인 풀림/한도초과) 재사용 |
| `AnthropicApiAgentRuntime` | Messages API tool-use 루프, 같은 BrowserToolbox 를 in-process 로 |
| `ScriptedAgentRuntime` | 테스트 대역 — 미리 정한 도구 호출 시퀀스 재생 (gym 테스트·상태기계 테스트용) |

- 시스템 프롬프트 = 역할/규칙 + 전역 가이드 + 도메인 가이드(§A8) + run 종류별 지시 + (재진입이면) FillLog/피드백.
- 텍스트 전용 LLM 호출(추출·생성·반성)은 기존 `LLMCallable` port 를 유지해 같은 두 경로(CLI/API)로.

## §A7 프로필 · 문서

- **Profile**: 인적사항 — 기본(이름·연락처·링크·학력·스킬·언어) + **추가 정보**(병역·보훈·장애·희망연봉·입사가능일·거주지역,
  전부 선택, `None` = 아직 모름 → 실행 중 `ask_user`, D10). 언어 중립 필드 + 표시 라벨 i18n.
- **Experience/Fact** (v2 Fact 모델 일반화): entity(회사/프로젝트/활동/교육) · 기간 · 역할 · fact 문장(지표 포함) · `skills`(v2 `tech_stack` 일반화) · 링크 · 첨부.
  저장은 중첩(Experience → 선택적 `sections`(= v2 block) → fact), 이력서 파이프라인은 여전히 평평한 `Fact` 를 받는다 —
  `FactSource` 구현이 `domain/experience_facts.py` 로 결정론적으로 펼친다(섹션 없으면 블록 `main` 하나, activity·education 은 블록 없이 fact 만).
- **저장**: 프로필·경험·답변·문서 메타 모두 `UnitOfWork` 의 repository(sqlite + memory 대역, 리비전 0002). cwd 기준 `config/*.yaml` 소스는 없다.
  `resume_guide.{platform}.md` 는 §A8 DB 전까지 데이터 디렉터리 `guides/` 에 둔다.
- **고유식별정보 거부** (절대 규칙 5): 주민등록번호(외국인등록번호 포함) 꼴을 두 겹으로 막는다 — Profile·Experience·Answer·DocumentMeta DTO
  생성 시점, 그리고 검증기를 건너뛴 값(`model_copy(update=)`·`model_construct`)을 위해 repository `save` 시점(sqlite·memory 공통).
  규칙은 `domain/unique_identifiers.py` 한 곳: NFKC 정규화(전각 숫자) + zero-width 제거 뒤, 생년월일 꼴 6자리 + 구분자(하이픈 계열·`.`·`_`·`/`·공백·없음)
  + 7자리. 에러 메시지·로그에 입력값을 싣지 않는다.
- **AnswerKB**: (정규화 질문 키, 답, 출처 지원 건, 갱신일). 에이전트가 먼저 조회, 없으면 `ask_user`.
- **Document**: 사용자 업로드 고정 파일 / 생성 파일(PDF) — 버전·생성 근거(fact_ids) 보관.
- **온보딩 추출**: 이력서 PDF/DOCX → LLM 구조화 추출 → 초안(사용자 검토 후 확정). 추출물도 스키마 검증 통과해야 저장.
- **공고맞춤 생성** (D11 기본 전략): 공고 본문 → 관련 fact 선택(v2 `select_relevant_blocks`) → 불릿 생성 → `ground_check` → 직군 템플릿 렌더.
- **직군 템플릿** (D12): `templates/{family}/resume.html.j2`, `portfolio.html.j2`(해당 시). family 별 섹션 구성·포트폴리오 필요도(`required|optional|none`) 는 데이터(`families.yaml`)로.
- **PDF 렌더**: 헤드리스 Chromium `page.pdf()` (Windows 호환). `PdfRenderer` port 유지, 기존 WeasyPrint 구현은 제거.
- **자소서 답변**: 문항·글자수 제한 → 근거 fact 선택 → 초안 → `ground_check` → 글자수 검증. 승인 큐에서 문항별 편집/재생성.

## §A8 가이드 (자연어 학습 메모리)

- `guides(scope: global | domain, domain, version, content_md, created_at, source_run_id, reason)`.
- **자동 반영 (D13)**: run 이 끝날 때(성공·실패·수정요청·사람 개입·SUBMIT_MISMATCH) 반성(reflection) LLM 콜이 교훈을 뽑아
  해당 scope 가이드에 새 버전으로 병합. 병합 결과가 길이 상한을 넘으면 압축 콜로 재작성(역시 새 버전).
- 모든 버전은 출처(run·피드백 원문)를 가진다. UI: 목록·diff·롤백·직접 편집.
- 가이드는 **프롬프트 텍스트일 뿐**이다. 하네스(§A4)·도구 표면(§A5)·설정을 바꾸는 통로가 없다.

## §A9 작업 큐 (JobRunner)

- `jobs(id, kind, application_id, status, attempt, payload, created_at, started_at, finished_at, error)` — SQLite 행 잠금 대신
  단일 프로세스 asyncio 러너가 소비. 브라우저 필요 작업(fill/revise/submit)은 **동시성 1**, 문서 생성·반성은 별도 동시성.
- 재시도는 인프라성 실패(브라우저 크래시·CLI 일시 오류)만. 도메인 실패(로그인 필요·입력 필요·하네스 차단)는 재시도 없이 상태 전이.
- UI 갱신: 폴링(TanStack Query) 기본, 필요 시 SSE.

## §A10 보안 · 설정

- 서버는 `127.0.0.1` 만. 모든 변경 API 는 `Origin` 검사 + 설치별 랜덤 토큰 헤더. MCP 엔드포인트는 run 별 일회성 토큰.
- 설정(`settings` 테이블 + 최초 `.env` 없이도 동작): `llm_backend=cli|api`, API 키(파일 권한 0600), `submit_mode`, 대기 타임아웃, 언어.
  `settings` 테이블 전까지의 우선순위: 환경변수 > (개발 모드일 때만) `./.env` > 사용자 설정 디렉터리 `.env`
  (`platformdirs.user_config_dir("auto-apply")`, `AUTO_APPLY_CONFIG_DIR` 로 이동 가능). 개발 모드 = `AUTO_APPLY_DEV=1`
  이거나 cwd 에 `name = "auto-apply"` 인 `pyproject.toml` 이 있을 때. 설치본을 아무 폴더에서 실행해도 그 폴더의 `.env` 가
  `DRY_RUN_ONLY`·`DATA_DIR`·API 키를 바꾸지 못하게 하려는 것이다(절대 규칙 2).
- CORS 는 `WEB_CORS_ORIGIN` 하나만 연다. `http(s)://127.0.0.1` / `http(s)://localhost` 출처만 허용하고 `*` 등은 설정 로드에서 거부한다.
- 기동 마이그레이션은 한 트랜잭션(pysqlite 트랜잭션 레시피 + `transactional_ddl`)이라 실패해도 스키마가 반쯤 남지 않는다.
  실패하면 `auto-apply` 는 DB 경로·원인 한 줄을 로그로 남기고 종료 코드 1 로 끝난다.
- 로그: structlog, **모든 로그에 `application_id`·`run_id` 구조화 필드.**
