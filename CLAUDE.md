# auto-apply v2

구직 지원 자동화. **Temporal**이 워크플로우/상태를, **AI**가 이력서와 Recipe를, **Playwright**가
검증된 Recipe 실행을 담당한다. 사람은 **Telegram**으로 승인한다.

> 작업 시작 전 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)를 읽어라. 아래는 그 문서의 실행 규칙 요약이며,
> 충돌하면 ARCHITECTURE.md가 기준이다. 설계를 바꿀 때는 코드와 그 문서를 같이 수정한다.

## 명령어

```bash
make setup     # uv sync + .env 생성
make up        # 인프라 기동 (postgres/temporal/temporal-ui/minio) → UI: localhost:8080
make migrate   # Alembic 마이그레이션 적용 (REPOSITORY=postgres 일 때)
make check     # lint + type + arch + test  ← 커밋 전 필수 (워크플로우 테스트 포함, ~2분)
make test      # 단위/계약/워크플로우 테스트 (docker·브라우저 불필요)
make test-fast # 개발 중 빠른 반복 — Temporal 을 띄우는 테스트까지 뺀다 (~5초, 게이트 아님)
make test-all  # 전체 (make up + playwright/weasyprint 필요)
make api       # FastAPI dev server
QUEUE=ai make worker
```

인프라는 Docker, **api/worker는 호스트에서 uv로 실행**한다 (디버깅 편의).

## 절대 규칙 (위반 시 설계가 무너진다)

1. **Temporal = 실행 상태, DB = 비즈니스 데이터.** 진행 단계를 DB에 이중 관리하지 않는다.
   `applications.status`는 projection이고, 이를 쓰는 통로는 `persist_state` activity **하나뿐**이다.
2. **AI는 제출하지 않는다.** AI는 `AutomationRecipe`(데이터)를 만들고, Playwright가 그것을 실행한다.
   LLM 출력은 반드시 Pydantic 검증 + 정책 검증을 통과해야 실행 계층에 닿는다.
3. **모든 I/O는 activity 안에서만.** workflow 코드는 결정적이어야 한다.
4. **되돌릴 수 없는 행위(최종 submit)는 사람의 승인 뒤에서만.** `DRY_RUN_ONLY=true`가 기본값이다.

## 계층 규칙 — `make arch`가 강제한다

```
domain      순수 (프레임워크/어댑터 무의존)
contracts   workflow-safe DTO + activity stub. 벤더 SDK 금지 (temporalio만 예외)
ports       Protocol 정의. 구현을 모른다
adapters    port 구현체. 상위 계층을 모른다
activities  port 주입받는 activity 구현
workflows   contracts/domain 만 import
bootstrap   ★ 어댑터를 생성하는 유일한 파일
api         라우터는 컨테이너에서 꺼내 쓴다
운영 진입점  telegram/·cli·watchdog·worker·schedule*·apply_intake·process_alerts·
            resume_cleanup — Temporal Client SDK 를 직접 써도 되고 workflows 를 import 해도
            된다. 단 **어댑터는 직접 만들지 않는다**(bootstrap 경유) — 이 층에 거는 유일한 계약
```

## 새 외부 의존성을 추가할 때 (순서를 지킬 것)

1. `ports/`에 Protocol 정의 — 벤더 타입을 시그니처에 노출하지 않는다
2. **예외 계약을 정한다** (`domain/errors.py`) — port가 새는 건 반환값이 아니라 예외 쪽이다
3. 구현 **2개** 작성: 실제 어댑터 + 오프라인/테스트 대역
4. `tests/ports/`의 contract test `params`에 추가 — 하나의 스위트가 모든 구현에 돌아야 한다
5. `bootstrap.py`의 `_build_*`와 `config.py`의 Literal에 선택지 추가

## Temporal 관련 주의 (자주 틀리는 곳)

- **activity 추가 절차**: `contracts/activity_defs.py`에 `@activity.defn(name=...)` stub 추가 →
  `activities/*.py`의 클래스에 **같은 이름**으로 구현 → 그 클래스 `all()`에 등록 → `worker.py`의 큐에 배선.
- `activity_defs.py`의 stub을 `Worker(activities=[...])`에 등록하면 `NotImplementedError`가 난다.
- workflow 안에서 `datetime.now()`/`random`/`asyncio.sleep`을 쓰지 않는다 → `workflow.now()`, `workflow.sleep()`.
- 승인/예약 대기는 `workflow.sleep`이 아니라 `workflow.wait_condition(..., timeout=)`.
  그래야 대기 중 reschedule/cancel signal이 먹는다.
- 재시도 금지 에러는 `domain/errors.py`의 `NON_RETRYABLE`에 등록하고 RetryPolicy에 넘긴다.
- signal 핸들러는 **멱등**하게 (중복 승인은 무시). Telegram 버튼은 두 번 눌린다.
- **워커 재시작을 테스트할 땐 `Worker(..., max_cached_workflows=0)`**. sticky execution이 켜져 있으면
  서버가 죽은 워커의 sticky 큐로 계속 라우팅해서 테스트가 그냥 멈춘다 (에러도 안 난다).
- 도메인 예외는 Temporal을 건너면 `ApplicationError`로 감싸지고 원래 클래스는 `.type` **문자열**로만 남는다.
  workflow에서 `isinstance`가 아니라 `e.cause.type == "..."`로 분기해야 한다.

## 개발 프로세스 (필수)

**테스트 없는 구현은 완료가 아니다.**

- 기능을 구현하면 **같은 커밋에 테스트를 넣는다.** 나중에 추가하지 않는다.
- 구현 후 반드시 `make check`를 **실행해서 통과를 확인**한다. "통과할 것이다"로 보고하지 않는다.
- 검증 결과를 보고할 때는 **실제 출력**을 근거로 말한다. 실패했으면 실패했다고 말한다.
- 새 port를 만들면 contract test, workflow를 만들면 `WorkflowEnvironment` 테스트,
  LLM/Recipe 스키마를 건드리면 회귀 테스트를 함께 만든다.
- 테스트 마커는 **"무엇이 있어야 도는가"로만** 가른다. 아무것도 안 붙이면 기본 실행이다.
  - `docker` — `make up` 인프라(postgres/minio) 필요
  - `native` — 이 머신에 설치된 외부 바이너리/시스템 라이브러리 필요 (playwright 브라우저,
    agent-browser CLI, weasyprint 의 cairo/pango)
  - `temporal` — Temporal 테스트 서버를 띄운다. **Docker 가 아니므로 `make check` 에서 돈다** —
    느릴 뿐이라 빠른 반복용 `make test-fast` 에서만 빠진다.
  예전엔 `integration` 하나가 "Docker 필요"와 "Temporal 서버만 필요"를 뭉쳐서, 규칙이
  요구하는 워크플로우 테스트를 규칙의 게이트가 한 개도 안 돌리고 있었다(2026-08-23 수정).

**커밋 규칙 (개발 초기 단계)**

- **큰 기능 단위로 하나씩 커밋한다.** (예: "승인 흐름 + durable timer", "Playwright executor")
  여러 기능을 한 커밋에 몰지 않고, 반대로 파일 하나 고칠 때마다 커밋하지도 않는다.
- **대화 턴이 끝나면 자동으로 커밋한다.** 사용자에게 커밋해도 되는지 물어보지 않는다 —
  요청을 기다리는 게 기본 동작이 아니라, 매 턴 끝에 커밋하는 게 기본 동작이다. 한 턴 안에
  기능이 여러 개 섞였으면 위 기능 단위 규칙대로 나눠서 커밋한다.
- 커밋 전 `make check` 통과가 전제다. 깨진 상태를 커밋하지 않는다.
- 설계가 바뀌면 `docs/ARCHITECTURE.md`를 같은 커밋에서 수정한다.
- **커밋에 `Co-Authored-By: Claude` 트레일러를 넣지 않는다.**

## 코드 컨벤션

- **절대 import만** (`from auto_apply.x import y`). 상대 import는 ruff TID252가 막는다.
- DTO는 `extra="forbid"` + `frozen=True`. LLM이 만든 필드가 실행 계층까지 흐르지 않게 한다.
- 열거형은 `StrEnum` (Temporal payload·DB 저장 모두 안전).
- Protocol의 속성은 **`@property`로 선언**한다. 일반 속성은 invariant라서 구현체가 더 구체적인
  타입을 노출하면 타입 체크에 실패한다.
- 로그는 structlog, **모든 로그에 `workflow_id`를 구조화 필드로** 넣는다. DB·S3·트레이스를 잇는 유일한 키다.
- 주석은 "왜"만 쓴다. 설계 근거는 `ARCHITECTURE.md §N`으로 참조한다.
- **파일 하나를 너무 키우지 않는다.** 대략 200줄을 넘어가면 책임을 쪼갤 지점을 찾는다
  (예: action dispatch를 별 모듈로, 큰 워크플로우는 헬퍼 파일로). 무조건적인 상한이 아니라
  "한 파일 = 한 책임"이 흐려지는 신호로 쓴다.

## 하지 말 것

- CAPTCHA/추가 인증(SMS 등) 우회·자동 해결 — 절대 하지 않는다. Recipe 실행 중이면
  `CaptchaEncountered`를 던지고, 로그인 중이면(`scripts/auto_login.py`)
  `domain/login_flow.detect_login_outcome`이 감지해 실패시킨다. 두 경우 다 사람에게 넘긴다.
- 새 플랫폼 계정을 대신 만들지 않는다 — 본인인증(SMS 등)이 필요해 사람만 할 수 있다. 자동
  로그인(`scripts/auto_login.py`)은 항상 **본인이 이미 가진 계정**의 자격증명(`.env`)만 쓴다.
- `DRY_RUN_ONLY`를 사용자 확인 없이 끄지 않는다.
- AI가 만든 Recipe를 `active`로 바로 올리지 않는다 (`draft → candidate → active`, 사람 승격).
- `applications.status`를 `persist_state` 밖에서 UPDATE하지 않는다.
- 플랫폼 rate limit(`platform_policies`)을 우회하는 코드를 추가하지 않는다.

## 현재 상태

**M0 완료** — 스캐폴딩/툴체인/계층 가드/contract test 패턴 + `PingWorkflow` 스모크.

**M1 완료** — 승인 흐름을 `ConsoleNotifier`로 먼저 완성한 뒤(durability는
`tests/workflows/test_durability.py`가 "워커를 강제 종료해도 예약이 살아있다"로 증명),
`TelegramNotifier` + `/applications` 승인 엔드포인트(§7) + 텔레그램 인바운드 경로 둘(웹훅
`POST /telegram/webhook`, 롱폴링 `telegram/listener.py` — `make telegram-listen`)을 얹었다.
nonce(§6, 오래된 버튼 재사용 방지)는 **`ApplicationWorkflow` 가 직접 들고 검증**한다 —
Notifier 어댑터 메모리에 뒀더니 발급 프로세스(worker)와 검증 프로세스(webhook 서버/리스너)가
갈라질 때 항상 실패했다(라이브 스모크테스트로 실측, `workflows/application.py`의
`_decision_nonce`/`_nonce_ok` 참고). `NOTIFIER=telegram` + `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_ALLOWED_CHAT_IDS`로 켠다. 로컬 개발은 공인 URL이 없으므로 웹훅 대신
`make telegram-listen`(getUpdates 롱폴링)을 쓴다.

**M2 완료** — Playwright executor(`adapters/executor/playwright.py`, `EXECUTOR=playwright`),
`RecipeSource`(JSON 파일 기반), `_resolve_mode`(dry_run/supervised/live 분기)에 이어
`application_attempts` 감사 로그를 얹었다. `AttemptRepository` port(파일 + in-memory,
`tests/ports/test_repository_contract.py`) + `record_attempt` activity로 실행 1회 = 1행을
남긴다 — `(application_id, attempt)` 기준 멱등 upsert라 submit 직전 `UNKNOWN`("submitting")
선기록 → 결과로 덮어쓰는 패턴(§5)이 가능하다. 실행 activity가 실패해도 `verify_submission`을
먼저 돌려 실제로는 제출됐는지 확인한 뒤에야 `needs_human`으로 넘긴다(부분 제출 위험 방어,
§5) — 그 로직은 `workflows/_execution.py`로 분리했다(`application.py`가 감사 로그까지
넣으면 한 파일 책임이 흐려져서). 이어서 Postgres/Alembic(`adapters/repository/postgres.py`,
`REPOSITORY=postgres`)을 얹었다 — 같은 `UnitOfWork` port를 `INSERT ... ON CONFLICT`로
구현한 세 번째 대역이다(§9.1). 테이블은 `applications`(§4 ERD)를 그대로 정규화하지 않고
DTO를 JSONB `payload`에 담고 조회/유니크 키만 실제 컬럼으로 뺐다 — port가 요구하는 계약이
"멱등 upsert + 이력 조회"뿐이라 파일 어댑터와 같은 모양을 유지하는 쪽을 택했다. contract
test에 `postgres` 파라미터를 추가했고(`@pytest.mark.integration`, `make up` 필요),
`alembic/versions/`의 초기 마이그레이션은 실제 DB에 대고 autogenerate + upgrade/downgrade
왕복까지 검증했다. 기본값은 여전히 `REPOSITORY=file`이다 — 바꾸는 결정은 사용자 몫으로 남긴다.

**M3 완료** — `SimpleResumeGenerator`/`SimpleResumeReviewer`가 자리만 잡아둔 상태였던 걸
Fact 기반으로 채웠다. `Fact`(§4)는 `FactSource` port(`config/facts.yaml`이 원본, `MatchingConfigSource`와
동일 패턴 — 캐시 없이 매번 새로 읽음) + `YamlFactSource`/`StaticFactSource` 두 대역. 생성 흐름은
`retrieve_facts` → `select_relevant_facts`(`domain/resume_matching.py`, keyword 겹침 랭킹 —
match_skills/select_projects를 한 단계로 합침, 0건 매칭이면 필터링 없이 전체 반환) → LLM 구조화
생성(`ai/schemas.py`의 `ResumeContentSchema`) → `ground_check`(같은 파일, review 게이트의 첫
체크) — highlight마다 근거 `fact_id`가 있는지, 그 id가 실제 Fact에 존재하는지를 본다. 스키마
위반 시 §5대로 generator 내부에서 최대 2회 재프롬프트하고, 그래도 실패하면
`LLMSchemaViolation`을 `NON_RETRYABLE`에 태워 activity 레벨 재시도를 끊는다(같은 실패가
반복될 뿐이라). `AnthropicLLM`(`adapters/llm/anthropic.py`, tool-use로 구조화 출력 강제)을
`LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`로 켠다 — 기본값은 여전히 `stub`.
`ports/resume.py`의 오케스트레이션 프레임워크 판단(§9.2)은 M3에서도 **보류를 재확인**했다 —
파이프라인이 여전히 분기·병렬 없는 선형 체인이라 도입 기준을 못 채운다. LangGraph로 못박지도
않았다 — 소규모 프로젝트엔 PydanticAI가 더 맞을 수 있어 그쪽도 검토 중이라, `ai/`를 어느
프레임워크 타입에도 묶지 않고(순수 Pydantic + 문자열 함수) 나중에 어느 쪽으로든 같은 포트
뒤에서 갈아끼울 수 있게만 열어뒀다.

**M3 연장 — 경력/프로젝트 블록 구조 + 실제 PDF 출력.** `summary`+`highlights` 뿐이던 스키마로는
원티드 PDF 내보내기 같은 실제 이력서 문서(회사 헤더 + 하위 블록별 불릿·기술스택, 개인 프로젝트,
학력, 스킬 태그, 언어)를 못 만들어서 확장했다. `Fact`에 `entity`/`entity_label`/`entity_period`/
`block`/`block_label`/`block_period`를 추가해(§4) 회사명·기간·블록 제목을 **결정론 코드로**
조립하고(`domain/resume_blocks.group_facts_for_resume` → `FactBlock`), LLM은 그 블록 안에서
불릿 문장만 쓴다(`ai/schemas.py`의 `BlockBullets`) — Recipe와 같은 "AI는 생성만, 판정·조합은
코드" 철학의 연장. 개인 프로젝트는 여러 개일 수 있어 `select_relevant_blocks`로 job 관련도 상위
N개만 추리고(경력은 전부 유지), `ground_check`는 `career[].blocks[].bullets`/`projects[].bullets`
까지 재귀 검사하도록 확장했다. 이름·연락처·학력 상세·스킬 태그·언어처럼 서술이 필요 없는 정형
정보는 Fact가 아니라 별도 `ProfileSource` port(`config/profile.yaml`, `FactSource`와 동일 패턴)
에서 와서 LLM을 거치지 않는다. `adapters/resume/_assemble.py`가 이 셋(결정론 블록 메타데이터 +
LLM 불릿 + Profile)을 `contracts/resume_content.AssembledResume`로 합쳐 `ResumeDraft.content`에
담고, `PdfRenderer`는 그 모양만 알면 된다. `PdfRenderer`는 §9.1에서 후보로만 적어뒀던
`WeasyPrintPdfRenderer`를 실제로 구현했다(HTML/CSS 템플릿은 `adapters/pdf/_template.py`로 분리해
weasyprint 없이도 순수 함수로 테스트) — macOS(Homebrew)에서 weasyprint가 dlopen 하는
libgobject/pango/cairo 를 찾으려면 `DYLD_FALLBACK_LIBRARY_PATH`가 필요해서, 셸 설정에 기대는
대신 어댑터 모듈 로드 시점에 보정한다. weasyprint 렌더 테스트는 시스템 라이브러리가 있어야 돌아서
`@pytest.mark.integration`(`make up` 불필요), `PDF_RENDERER` 기본값은 `weasyprint`다.

**M3 연장 — `ClaudeCodeCliLLM`(API 키 대신 로컬 Claude Code 구독).** `LLMClient`의 세 번째
구현(`adapters/llm/claude_code_cli.py`)으로, `ANTHROPIC_API_KEY` 종량제 대신 이 머신에
로그인된 Claude Code 구독으로 `claude` CLI 를 headless subprocess 로 부른다.
`--bare`는 안 쓴다 — OAuth/keychain을 안 읽고 API 키 인증을 강제해서 정확히 피하려는
과금 방식으로 되돌아간다(실측, `docs/ARCHITECTURE.md` §11.2c). 대신 `--tools ""`·
`--strict-mcp-config`·`--disable-slash-commands`·`--setting-sources ""`·`--system-prompt`
교체로 하네스(빌트인 툴/MCP/스킬/CLAUDE.md/기본 시스템 프롬프트)를 낱개로 걷어내고, `--model`을
항상 명시해 CLI 의 모델 라우팅용 Haiku 분류기 호출도 없앤다. 구조화 출력은 `--json-schema` +
`--output-format json`의 `structured_output` 필드로 받는다(`AnthropicLLM`과 같은
`LLMSchemaViolation` 계약 유지). 캐시는 `LLMClient`에 선택 파라미터 `cache_key`를 추가해
확보한다 — 새 프로세스 1회성 호출은 프롬프트가 같아도 캐시가 전혀 안 붙는다는 걸 실측했고
(`--resume`으로 세션을 이어야만 이전 턴이 캐시로 읽힘), `SimpleResumeGenerator`가
`cache_key=user_id`를 넘겨 같은 사용자의 Fact/Profile 프리픽스를 여러 공고 생성에 걸쳐
재사용한다(세션은 턴수·TTL 상한을 넘기면 새로 판다). `AnthropicLLM`도 같은 파라미터로
`cache_control: ephemeral`을 프롬프트에 붙이도록 함께 확장했다. `LLM_PROVIDER=claude_cli` +
`CLAUDE_CLI_BINARY`/`CLAUDE_CLI_MODEL`/`CLAUDE_CLI_MAX_BUDGET_USD`로 켠다 — 이 머신에
`claude login`이 이미 돼 있어야 한다. 기본값은 여전히 `stub`.

**M3 연장 — claude CLI 장애 텔레그램 알림.** `ClaudeCodeCliLLM`이 재시도로 안 풀리는 두 실패
(로그인 풀림·구독 사용량 한도초과)를 실측 시그니처로 구분해 `LLMAuthRequired`/`LLMQuotaExceeded`
(`domain/errors.py`, 둘 다 NON_RETRYABLE)로 던지고, `ResumeWorkflow`가 `generate_resume`/
`review_resume` 실패를 감싸서 `e.cause.type`으로 이 둘을 알아보면 재던지기 전에 `notify`
activity(승인 흐름과 같은 `Notifier` 채널)를 `task_queue=QUEUE_DEFAULT`로 건너 호출해 사람에게
알린다(§11.2c). 타임아웃 등 분류 안 된 실패는 여전히 `LLMExecutionError`로 재시도 대상이라
알림을 안 보낸다.

**M3 연장 — REVISE(수정요청), 텔레그램 3번째 갈래.** 승인/거절 2갈래뿐이던 `DecisionKind`에
`REVISE`를 추가했다(`RevisionScope.SPECIFIC/GENERAL`로 반영 범위를 가른다) — "자기소개 더
짧게" 같은 실시간 프롬프팅 요구가 나온 세션에서 설계하고 이번 세션에서 구현했다(메모리
resume-revise-feedback-design). **SPECIFIC**은 영속 저장 안 함 — `ReviseSignal.feedback`이
`GenerateResumeRequest.feedback`으로 그 재생성 1회에만 흐른다. **GENERAL**은
`config/resume_guide.md`(신설, `GuideSource` port + `FileGuideSource`/`StaticGuideSource`,
`FactSource`와 같은 캐시-없이-매번-읽기 패턴)에 영속되지만 LLM이 전문을 재작성하지 않고
`{old, new}` 치환 쌍만 내고(`ai/schemas.GuidePatchSchema`) `domain/guide_patch.apply_patch`가
`old`가 정확히 1번 매치될 때만 적용한다 — 여러 규칙이 섞인 가이드에서 전문 재작성을 시키면
지시 안 한 규칙이 조용히 사라질 위험을 피하려는 선택(Recipe의 "AI는 생성만, 조합은 코드"
철학의 연장). GENERAL은 반영 전 **사람이 diff를 한 번 더 승인**해야 한다(§CLAUDE.md "되돌릴
수 없는 지점엔 사람" — 가이드는 이후 모든 생성에 영향을 주는 레버라 되돌리기 어려운 축)는
설계 세션에서 사용자가 명시적으로 확정한 방향이다. `ApplicationWorkflow`는 승인 대기를
while 루프로 바꿔 REVISE를 반복 처리하고(`MAX_REVISIONS=3` 초과 시 `needs_human`), 재생성/
가이드-patch 로직은 `workflows/_revision.py`로 분리했다(`_execution.py`와 같은 이유 —
`application.py` 한 파일에 다 넣으면 책임이 흐려진다). 가이드 patch용 2차 승인은 본 승인과
별도의 nonce/decision 슬롯(`_guide_decision`/`_guide_nonce`, signal
`approve_guide_patch`/`reject_guide_patch`)을 쓴다 — 섞으면 "가이드 반영 승인" 클릭이 "지원
승인"으로 잘못 해석될 수 있어서다. 텔레그램 UX는 REVISE 버튼 → scope 선택(이번만/항상) →
ForceReply로 자유 텍스트 피드백을 받는 3단계인데, 이 자유 텍스트를 어느 application/nonce/
scope에 연결할지가 프로세스 경계(worker vs webhook/listener) 문제였다 — nonce와 같은 이유로
어댑터/서버 메모리에 상태를 못 둔다. `[revise:{application_id}:{nonce}:{scope}]` 태그를
ForceReply 프롬프트 본문에 실어 보내고 사용자의 답장이 담아오는 `reply_to_message.text`에서
그 태그를 파싱해 복원하는 방식(상태 없이 왕복)으로 풀었다 — 이 세션에서 사용자에게 직접
확인받은 방향이다. `TelegramNotifier`에 이 3단계 전송 메서드(`send_scope_picker`/
`send_feedback_prompt`)를 추가했지만 `Notifier` port는 안 건드렸다 — nonce 발급을 동반하는
`request_decision`과 달리 이 둘은 안내 메시지일 뿐이라 port 표면을 넓힐 필요가 없었고,
`telegram/bridge.py`는 구조적 Protocol(`_RevisableNotifier`, `@runtime_checkable`)로만
호출해서 "Telegram이라는 단어를 모르는 port" 원칙(§11.6)을 지켰다. `POST
/applications/{id}/revise`(REST, 텔레그램 없이도 트리거 가능)도 approve/reject와 같은 모양으로
얹었다.

**공고 수집·매칭** (M1과 별도 트랙) — `JobSource`(wanted/saramin/jasoseol) + 순수 domain
매칭(`job_screening`/`job_applicability`) + `JobCollectionWorkflow` + Temporal Schedule(cron)
배선까지 구현됨. `uv run python -m auto_apply.cli collect-schedule`로 등록/갱신(idempotent),
`collect-unschedule`로 삭제, `collect --platforms wanted`로 수동 1회 실행. 자세한 설계는
ARCHITECTURE.md §11.2b.

**워크플로우 능동 감시 watchdog** (workflow-failure-visibility-backlog §3, `de56dcd`의 나머지
스코프) — `watchdog.py`(`make watchdog`)가 Temporal visibility API(`list_workflows`)를 주기
폴링해 FAILED/TERMINATED/TIMED_OUT으로 끝난 워크플로우를 `Notifier`로 알린다. `_execute()`
try/except 픽스(=알고 있는 실패 지점을 워크플로우 안에서 잡는 것, Temporal 커뮤니티 표준
권고)로 못 덮는 부분 — 코드 버그·사람의 실수로 인한 terminate·workflow_execution_timeout처럼
워크플로우 코드가 아예 더 못 도는 종료 — 를 잡는 프로세스 밖 백스톱이다. 새 port 없이 `cli.py`/
`schedule.py`와 같은 운영 진입점으로 Temporal Client를 직접 쓴다. 재시작 사이 워터마크를
영속화하지 않는다(`WATCHDOG_LOOKBACK_MINUTES`만큼 재훑음 — `telegram/listener.py`와 같은
트레이드오프, 놓치는 것보다 중복 알림이 싸다). 자세한 설계와 근거는 ARCHITECTURE.md §11.2d.

**wanted 이력서 첨부파일 정리** (wanted-resume-list-cleanup-backlog) — Recipe가 지원마다
`res_<hex>.pdf`로 이력서를 새로 렌더링해 업로드해서 지원 1회 = 계정에 남는 고아 파일 1개인
문제를 `resume_cleanup.py`(`make resume-cleanup`, 기본 dry-run·`ARGS="--yes"`로 실제 삭제)로
해결. wanted 삭제 API(`DELETE /api/chaos/resumes/v1/{key}`)가 storage_state 쿠키 인증만으로
동작하는 걸 agent-browser 라이브 탐색으로 확인 → `AttachmentManager` port +
`WantedAttachmentManager`/`FixtureAttachmentManager` + 순수 판정 함수
`domain/resume_cleanup.select_deletable`(포트폴리오/직접 업로드/원티드 자체 이력서는 이름이
패턴에 안 맞아 자동 보존) 구현·테스트·커밋 완료(7be2703, main), 라이브로 실제 계정 정리까지
검증(14→11개). 자세한 설계는 ARCHITECTURE.md §11.2e.

**M4 `AutomationRepairWorkflow`** (§2.4) — phase 1(recipe 버전관리 write path +
`domain/recipe_policy.py` 정책검증, `e8a2b5f`)에 이어 나머지 전 구간(LLM diff 제안 →
정책 검증 → 샌드박스 dry-run → candidate 저장 → Telegram 승격 승인 → `ApplicationWorkflow`
연동)을 구현했다. `ai/schemas.RecipeDiffSchema`가 `actions`에 `contracts.recipe.Action`을
그대로 재사용해서 Action의 model_validator(selector 필요 여부 등)가 재프롬프트 루프
(`activities/repair.py`, `SimpleResumeGenerator`와 같은 패턴) 안에서 공짜로 실행된다.
`workflows/repair.AutomationRepairWorkflow`가 다이어그램을 그대로 구현하고
(`MAX_SANDBOX_ATTEMPTS=2`), 샌드박스 dry-run은 새 실행 모드 없이 기존
`ExecutionMode.DRY_RUN`을 그대로 쓴다. 승격은 "supervised 실행이 성공하면 자동 승격"이
아니라 repair 워크플로우 자신이 그 자리에서 Telegram 승인을 받아 끝낸다 — `SUPERVISED`
모드가 실행 중 사람을 멈춰 세우는 메커니즘(아래 SUPERVISED 체크포인트 항목에서 이후 구현)이
이 시점엔 아직 실행기에 없었고, 있어도 승격 여부가 임의의 미래 지원 건 실행 결과에 걸리는
건 `RepairResult`를 동기적으로 기다리는 `ApplicationWorkflow` 흐름과 안 맞는다는 판단은
그대로다. child workflow dedupe(`repair-{platform}-{form_hash}`)는 phase 1에서 "전례 없는 패턴"으로
미해결로 남겼던 지점인데, "이미 도는 수선에 붙어서 기다리기"는 채택하지 않고
`WorkflowAlreadyStartedError`를 잡아 그 지원 건만 포기시키는 쪽으로 결정했다(Temporal
워크플로우 코드 안에서 child가 아닌 임의 워크플로우의 완료를 기다릴 표준 API가 없어서 —
`workflows/_repair.py`). `ApplicationWorkflow._execute`가 `RecipeExecutionError`를 만나면
§2.2 pseudocode의 "attempt in (1, 2)"대로 수선을 한 번 시도하고 recipe를 재조회해 딱 한 번
더 실행한다. Telegram 승격 승인은 `DecisionRequest.repair_promotion`(신규)으로 갈래를
타고, `application_id` 필드에 `f"{platform}-{form_hash}"`를 담아 `telegram/bridge.py`가
`pa`/`pr` 콜백에서 `repair-{...}` workflow id를 복원한다. 구현·유닛/워크플로우/e2e
테스트·`make check` 통과 완료. 자세한 설계와 결정 근거는 ARCHITECTURE.md §2.4.

**SUPERVISED 페이지 경계 체크포인트** (§2.4c) — `ExecutionMode.SUPERVISED`가 이름만 있고
실제로는 `LIVE`와 동일하게 그냥 제출까지 진행되던 갭을 메웠다. "인적사항 완료 → 스샷 승인
→ 자기소개서 페이지 → 반복" UX를 위해, 단일 `execute_application` activity 호출 안에서
브라우저를 계속 띄운 채 페이지 경계마다 `activity.heartbeat()`로 생존신호를 보내며 짧게
(기본 30분) 승인을 폴링 대기하는 방향(디태치드 세션-영속 인프라도, 매번 처음부터 재실행도
아닌 절충)으로 확정해 구현했다. `Action.checkpoint`(신규, recipe가 페이지 경계를 표시) +
`SUBMIT`은 이 플래그 없이도 SUPERVISED에서 항상 강제 체크포인트(CLAUDE.md 절대규칙 4) +
`CheckpointStore` port(`FileCheckpointStore`/`InMemoryCheckpointStore`, nonce 발급
프로세스(worker activity)와 승인 프로세스(webhook/리스너)가 갈라져 프로세스 메모리로 공유가
안 된다는 점은 기존 nonce와 같지만 여기서 기다리는 건 워크플로우가 아니라 activity 자신이라
signal을 못 쓴다) + `CheckpointWaiter`(port 아님, Notifier/CheckpointStore/BlobStore/IdGen
조합 클래스, `adapters/executor/_checkpoint.py`) + `RecipeExecutor.run()`에 `heartbeat`
키워드 인자 추가(activity가 `temporalio.activity.heartbeat`를 plain callable로 넘겨 adapters
레이어가 temporalio를 안 봐도 되게) + `PlaywrightExecutor` 배선 + `CheckpointDeclined`(거절/
타임아웃 둘 다 이 하나로, `NON_RETRYABLE`) + 텔레그램 `ca`/`cr` 콜백(워크플로우 signal이
아니라 `checkpoint_store.record_decision` 직접 호출)까지 구현·유닛/통합 테스트·`make check`
(+ 실제 Postgres/Temporal/Playwright integration까지) 통과 완료. 이어서 `AgentBrowserExecutor`
에도 같은 `CheckpointWaiter` 배선을 추가해 `EXECUTOR=agent_browser`도 체크포인트를 지원한다
(스크린샷만 CLI `screenshot` 서브커맨드로 찍는 차이). 자세한 설계는 ARCHITECTURE.md §2.4c.

**`/recipes/{platform}` 엔드포인트** (§7 백로그) — 승격이 손으로 recipe JSON의 `status`를
고치거나 M4 Telegram 승인 흐름으로만 가능했던 갭을 메웠다. `RecipeSource` port가 이미
`versions()`/`promote()`로 invariant(candidate만 승격 가능, 승격 시 기존 active는
deprecated로)를 강제하고 있어서 라우터(`api/routers/recipes.py`)는 그 port를 얇게
노출하기만 한다 — `GET /recipes/{platform}`은 버전 목록(없으면 빈 리스트), `POST
/recipes/{platform}/promote`는 `PolicyViolation`을 409로 매핑한다. workflow signal/query가
없는 순수 조회+상태전이라 `applications` 라우터 테스트와 달리 `WorkflowEnvironment` 없이
컨테이너만 갈아끼운 가벼운 테스트로 검증했다(`tests/api/test_recipes_api.py`).

**S3BlobStore** (§11.2 백로그) — `bootstrap.py`의 `STORAGE=s3` 분기가 `NotImplementedError`만
던지던 갭을 메웠다. `boto3` 동기 클라이언트를 `LocalBlobStore`와 같은 패턴으로
`asyncio.to_thread`에 태우고, MinIO는 virtual-hosted-style DNS를 못 풀어서
`addressing_style="path"`로 고정했다. `ClientError`의 `NoSuchKey`/`404`/`NotFound` 코드를
`BlobNotFound`로 매핑해 `LocalBlobStore`/`InMemoryBlobStore`와 같은 예외 계약을 지킨다.
contract test에 `s3` 파라미터를 추가했는데(`@pytest.mark.integration`, `make up` 필요),
postgres 때와 같은 이유로(postgres-integration-test-data-wipe-hazard) 매 테스트 전 버킷을
통째로 비우는 방식이라 운영 `S3_BUCKET`과 분리된 `S3_TEST_BUCKET`(기본 `auto-apply-test`)을
새로 뒀다. 버킷은 없으면 `head_bucket`/`create_bucket`으로 첫 호출 시 lazy 생성한다. `STORAGE`
기본값은 여전히 `local`이다.

**wanted `verify_submission` 구현** — `WantedPlatformAdapter.verify_submission`이 항상
`unverified`를 반환하던 TODO를 메웠다. agent-browser 라이브 탐색(2026-08-20)으로 "내 지원
현황" API(`/api/v1/applications`)가 `job_id` 쿼리로 필터링되고, numeric `user_id`를 요구하며
(storage_state 엔 쿠키만 있어 `/api/v1/me`로 먼저 조회), `create_time`이 `WantedAttachmentManager`
의 `update_time`과 같은 타임존 표기 없는 KST 값이라는 걸 확인했다. `VerifyInput`에 `job_id`/
`since`를 추가해(_execution.py가 `job.job_id`/`started_at`을 채워 보낸다) 과거의 무관한
지원 이력으로 오탐(verified=True)하지 않게 했다 — `since`(워크플로우 시각, UTC) 기준 5분
여유(`_CLOCK_SKEW`)로 wanted 서버와의 시계 오차만 흡수한다. `_execution.py`의 verify_submission
activity 호출을 감싸 `AuthRequired`(storage_state 만료) 등 activity 실패를 "확인 안 됨"으로
안전하게 떨어뜨리게 했고(이전엔 안 잡혀서 워크플로우가 조용히 FAILED 로 죽을 수 있었다 —
`_QUICK`에 `non_retryable_error_types`도 빠져 있던 걸 같이 고쳤다), storage_state 쿠키 로더는
`WantedAttachmentManager`와 겹치던 걸 `adapters/_wanted_auth.py`로 뺐다. 유닛 테스트 +
실제 wanted 계정 대상 라이브 검증(과거 지원 건 매칭/미래 since 오탐 방지/무관한 job_id 모두
확인) + `make check` 통과 완료.

**텔레그램 자유 텍스트 채팅 에이전트** — 승인/거절 버튼, REVISE ForceReply 답장처럼 정해진
경로 없이 그냥 채팅해도 도구를 골라 처리하도록 `telegram/agent.py`를 추가했다. 멀티턴
tool-use가 없는 `LLMClient`(complete/structured 뿐) 위에서 ReAct 루프를 직접 짰다 — 매 턴
`structured()`로 discriminator 필드 하나짜리 스키마(`AgentStep`, ai/schemas.py: action이
call_tool/respond를 가른다)를 강제해 "도구를 부를지 답할지"만 고르게 하고, 실제 실행은
`TOOLS` 레지스트리(코드)가 한다 — Recipe와 같은 "AI는 생성만, 판정·조합은 코드" 철학의
연장. 설계 세션에서 스코프를 먼저 확정했다: 행동성 도구도 허용하되 실제 mutate는 여전히
사람이 기존 버튼을 눌러야 일어난다 — `resend_pending_decision` 도구는 새 workflow query
`ApplicationWorkflow.pending_decision()`으로 nonce를 읽어와 `TelegramNotifier.resend_decision`
이 원래 승인/거절/수정요청 버튼과 동일한 콜백을 다시 보낼 뿐, 새 signal은 안 쏜다(절대규칙
4를 자연어 오인식으로 우회하지 않기 위함). 탐색 중 `ApplicationRepository`에 "지원 건 목록
조회"가 아예 없던 공백을 발견해 `list_recent(limit)`를 신설했다 — file/memory/postgres 세
구현 모두 순서는 보장하지 않는다(채팅 편의 용도라 강한 계약 불필요). 도구 카탈로그→프롬프트
조립은 `domain/chat_agent.py` 순수 함수(포트 무의존)로 뺐다. 도구 하나가 실패해도, LLM
호출 자체가 실패(스키마 위반/quota)해도 `handle_chat`은 예외를 던지지 않고 사과 메시지로
마무리한다(webhook 라우트가 500을 내지 않는 전제). `telegram_chat_agent_enabled=false`로
재배포 없이 끌 수 있다(기본 true). 구현·유닛/통합 테스트(workflow query, postgres
list_recent, webhook 라우팅 포함)·`make check` 통과 완료. 자세한 설계는 ARCHITECTURE.md §6.

**자동 로그인 정책 변경 + `scripts/auto_login.py`** — saramin `verify_submission` 재탐색
중(2026-08-21) `var/auth/saramin.json`이 예상보다 훨씬 빨리 만료돼(로그인 뒤 페이지 이동
몇 번 만에 재로그인 필요) 사람을 계속 불러야 했던 게 계기. 이 세션에서 사용자가 직접
정책 완화를 결정했다: "비밀번호를 코드가 타이핑하지 않는다"를 삭제하고, 대신 (1) 새 계정을
대신 만들지 않고 **본인이 이미 가진 계정**의 자격증명만 `.env`(`SARAMIN_USERNAME`/
`SARAMIN_PASSWORD`)에 두고, (2) CAPTCHA/추가 인증은 여전히 우회하지 않는다는 두 제약으로
대체했다(§ "하지 말 것"). credential vault 연동(agent-browser plugin 등)은 이번엔 보류하고
`.env` 평문으로 시작하기로 확정(나중에 바꿀 수 있는 결정으로 남김). CAPTCHA/추가인증/오타
판정은 `domain/login_flow.detect_login_outcome` 순수 함수로 분리해 실제 사이트 없이
테스트했다(`tests/domain/test_login_flow.py`) — 이 판정이 곧 "우회 안 한다" 안전장치라
글루 코드(`scripts/auto_login.py`, Playwright 직접 구동, headless=False 고정 — 사람인
WAF가 headless를 막는다는 기존 실측과 같은 이유)와 분리해 신뢰도를 확보했다.
`scripts/save_auth_state.py`(사람이 수동 로그인)를 대체하지 않고 보완한다 — CAPTCHA
감지 시 이 스크립트로 폴백. 구현·`make check` 통과 완료.

**텔레그램 채팅 에이전트 `start_applications` 도구 + `chat_llm` 모델 분리** — "상주 에이전트가
알아서 몇 건 지원해줘" 요청(2026-08-21)으로, 기존 조회/재전송 도구뿐이던
`telegram/agent.py`의 `TOOLS`에 실제로 `ApplicationWorkflow`를 시작시키는 첫 행동성 도구를
추가했다. "제출해줘 N건"은 새 공고를 라이브로 다시 수집하지 않고 `JobCollectionWorkflow`가
채워둔 `uow.jobs.actionable()` 캐시를 24시간 TTL 안에서만 재사용해 적합도 상위 N건을
고른다(응답 속도·플랫폼 rate limit 모두를 위한 사용자 결정). `application_id`는
`domain.job_identity.canonical_key(company, title)`을 그대로 써서 같은 공고로 두 번
지원을 시작하지 않는다 — Temporal 기본 `WorkflowIDReusePolicy.ALLOW_DUPLICATE`는 이전
실행이 COMPLETED로 끝난 뒤 같은 id 재시작을 막지 않는다는 걸 확인하고, 이 호출에서만
`REJECT_DUPLICATE`를 명시해 `WorkflowAlreadyStartedError`를 "이미 지원함" 신호로 쓴다.
실제 최종 제출은 이 도구가 시작한 워크플로우 안에서도 여전히 사람의 텔레그램 승인 뒤에만
일어난다 — CLAUDE.md 절대규칙 4의 진짜 불변식은 "워크플로우를 안 건드린다"가 아니라
"제출은 못 건드린다"임을 문서에 명시했다. 로직은 새 port 없이 `cli.py`/`watchdog.py`와
같은 운영 진입점 패턴으로 `apply_intake.py`에 뺐고, `telegram/agent.py`는 루프
오케스트레이션만 남기고 도구 구현(TOOLS 레지스트리 전체)은 `telegram/_agent_tools.py`로
분리했다(파일이 200줄을 넘어가던 신호). 별개로 도구 선택/응답 판단(`AgentStep`)은 이력서
생성보다 훨씬 가벼운 분류 작업이라는 사용자 지적으로 `LLMClient`를 하나 더 만들어
`c.chat_llm`(기본 모델 Haiku, `TELEGRAM_AGENT_MODEL`)으로 분리했다 — `c.llm`(이력서 생성)은
그대로 둔 채 `bootstrap._build_llm`에 `model` 오버라이드 인자를 추가해 같은 프로바이더로
다른 모델의 인스턴스를 하나 더 만드는 방식. `start_applications`는 `dry_run` 인자도 받는다
(같은 세션, 사용자 요청 — "테스트용으로도 가능하게") — true면 `client.start_workflow`를
아예 안 부르고 선정 로직(TTL/정렬/count)이 뭘 골랐을지만 보여준다. `DRY_RUN_ONLY`(§9.5,
실행 단계의 제출 여부)와는 다른 레벨이라 이름이 겹치는 걸 문서에 명시했고, Temporal을 안
건드리는 경로라 `WorkflowAlreadyStartedError` 기반 중복지원 dedupe는 이 경로에서 작동하지
않는다는 한계도 남겼다(후보만 보여줄 뿐). 구현·유닛 테스트(`apply_intake.py` TTL/정렬/
dedupe/dry_run, `telegram/agent.py` 도구 라우팅)·`make check` 통과 완료. 자세한 설계는
ARCHITECTURE.md §6.

**텔레그램 채팅 에이전트 `apply_by_url` 도구** — "wanted 링크 보내면 지원 프로세스 도는
기능 있냐"는 질문(2026-08-21)에 이어진 요청. `start_applications`가 캐시에서 자동 선정하는
것과 반대로, 사람이 URL을 직접 지정("이 링크 지원해줘")하면 그 공고 하나에 대해
`ApplicationWorkflow`를 시작한다. `uow.jobs.actionable()` 캐시를 안 거친다 — 사람이 이미
골랐으니 다시 스크리닝하지 않는다. 대신 `c.registry.for_url(url)` + `adapter.fetch_job(url)`
(`ApplicationWorkflow`의 `collect_job` activity가 쓰는 것과 같은 `PlatformAdapter` port)로
즉석에서 company/title을 얻어 `canonical_key`를 계산하고, `start_actionable_applications`와
같은 판정(REJECTED 제외 기존 이력이 있으면 시작 안 함)을 1건짜리로 적용한다. 플랫폼은 wanted로만
한정했다 — registry엔 saramin도 등록돼 있지만, 자소서 문항 있는 공고가 아직 라이브 검증이 안
끝나서(메모리 saramin-recipe-progress.md) 임의 링크를 사람 개입 없이 실행 트리거하기엔 이르다는
판단(사용자 요청으로 명시적 제한). 워크플로우 조립(`StartApplication` 구성 +
`REJECT_DUPLICATE` dedupe)은 `start_actionable_applications`의 루프와 `_start_workflow`
헬퍼로 공유해서 뺐다. 구현·유닛 테스트(`apply_intake.py`의 wanted 한정/dedupe/REJECTED 재시도,
`telegram/agent.py` 도구 라우팅)·`make check` 통과 완료. 자세한 설계는 ARCHITECTURE.md §6.

**공고수집·자동지원 Schedule 봇 탑재** — "공고수집 및 지원하기 스케줄링 기능 봇에
탑재해"(2026-08-22) 요청. 공고 수집은 이미 Temporal Schedule(cron)로 주기 실행됐지만
(공고 수집·매칭 트랙), "지원 시작"(`start_actionable_applications`)은 채팅으로만 트리거되던
갭을 메웠다. 새 `ApplyIntakeWorkflow`(activity 하나, `ApplyIntakeActivities` — 기존
`apply_intake.start_actionable_applications`를 그대로 재사용) + `schedule.py`의
`ensure_apply_intake_schedule`/`delete_apply_intake_schedule`(job-collection Schedule과
같은 create-or-update 패턴) + `cli.py apply-schedule`/`apply-unschedule`로
`APPLY_SCHEDULE_CRON`(기본 매일 10시)/`APPLY_SCHEDULE_COUNT`(기본 3건) 설정을 등록한다.
최종 제출은 그렇게 자동 시작된 `ApplicationWorkflow` 안에서도 여전히 사람의 텔레그램 승인
뒤에만 일어난다(절대규칙 4 — 자동화되는 건 "지원 프로세스 시작"까지). "봇에 탑재"는 텔레그램
채팅 도구 `schedule_status`/`set_schedule_enabled`(`telegram/_agent_tools_schedule.py`,
신설 — `_agent_tools.py`가 200줄을 넘어가서 분리)로 구현했다 — cron 시각·건수는 채팅으로
바꾸지 않고(LLM이 cron 표현식을 파싱하는 실패 위험을 피함, `.env`로 고정) 이미 등록된
Schedule을 pause/unpause 하는 on/off·상태조회만 채팅으로 노출한다. 실측(2026-08-22, 라이브
Temporal 서버): `describe()`가 돌려주는 `spec.cron_expressions`는 서버가 내부 캘린더
스펙으로 컴파일하며 비워버려서, `schedule_status`는 그 필드 대신 `c.settings`에 있는 등록값을
그대로 보여준다. 구현·유닛/워크플로우/실제 Temporal 라이브 등록·`make check` 통과 완료.
자세한 설계는 ARCHITECTURE.md §11.2f.

**알림 사각지대 전수 조사 + 보완** (2026-08-22) — "에러가 나도 알림이 안 오는 곳"을 전수
조사했다. watchdog(`watchdog.py`)은 Temporal 이 **닫은** 워크플로우(FAILED/TERMINATED/
TIMED_OUT)만 보므로, 그 정의 밖의 조용한 실패 네 부류가 로그에만 남고 있었다: (1) **성공으로
끝나는 실패** — `JobCollectionWorkflow` 는 플랫폼 실패를 결과 필드로 삼켜 COMPLETED 로 끝나고
(셀렉터가 바뀌어 `found=0` 이 되면 예외조차 안 난다), `ApplyIntakeWorkflow` 는 후보 0건이어도
정상 종료한다. (2) **상주 프로세스의 죽음** — worker 가 죽으면 워크플로우는 FAILED 가 아니라
Running 인 채로 멈춰서 watchdog 이 영영 못 잡고, listener 가 죽으면 승인 버튼이 조용히 안
먹고, watchdog 이 죽으면 감시 자체가 사라진다. (3) **감시가 눈이 먼 구간** — Temporal 접속이
끊긴 동안의 폴링 실패는 로그만 남아서 그 침묵이 "아무 문제 없음"으로 읽힌다. (4) **인바운드
처리 실패** — 리스너 dispatch 예외/웹훅 500 은 버튼을 누른 사람에겐 그냥 무응답이다. 넷 다
텔레그램으로 알리게 했다: 판정은 순수 함수 `domain/alerting.py`(`collection_alert`/
`intake_alert` — Recipe 와 같은 "판정은 코드" 철학, 임계치를 Temporal 없이 테스트로 고정),
전송은 새 port 없이 기존 `Notifier`. 상주 프로세스 크래시는 `process_alerts.run_guarded`
(`cli.py`/`watchdog.py` 와 같은 운영 진입점 계층)가 세 프로세스의 `main()` 을 감싸 알리고
재던진다 — `SystemExit`/`KeyboardInterrupt` 는 사고가 아니라 안 알린다. watchdog 은
`WATCHDOG_BLIND_ALERT_AFTER`(기본 3회) 연속 폴링 실패에 **정확히 한 번** 알리고 복구도 알린다.
인바운드는 문구를 `telegram/bridge.inbound_failure_message` 하나로 공유해 롱폴링
(`_process_updates(..., on_error=)`)·웹훅(`api/main.py` 예외 처리기)이 같은 말을 한다.
알림 전송 자체의 실패는 `notify_safely` 가 삼킨다 — 알림을 보내는 자리는 대부분 이미 뭔가
잘못된 지점이라, 거기서 알림이 또 터지면 원래 오류가 가려진다. 구현·유닛/워크플로우/API
테스트·`make check` 통과 완료. 자세한 설계는 ARCHITECTURE.md §11.2d.

**공고수집·자동지원 Schedule 설정을 DB로 이관** — 위 구현 직후 사용자가 즉시 정정했다:
"cron으로 하지 말고 서버에서 하면 설정파일 건드릴 필요도 없지 않냐" → "db테이블 하나 만들면
되고"(2026-08-22, 같은 세션). cron/건수를 `.env` 고정 + 봇은 on/off만 하던 걸, 시각(hour/
minute)·건수까지 채팅으로 바꾸도록 확장했다 — LLM이 cron 문법을 직접 만들면 안 된다는 원래
우려는 유지한다(`domain/schedule_cron.build_cron(hour, minute)`, 순수 함수가 여전히 조립을
전담). 새 `ScheduleConfig`(target당 최신값 1건) + `ScheduleConfigRepository` port를
`UnitOfWork`에 4번째 sub-repository로 추가해 memory/file/postgres 세 구현(+ Alembic
`schedule_configs` 테이블)을 얹었다. `schedule_config.py`(신설, 운영 진입점)가 "DB에 있으면
그 값, 없으면 `.env` 시드값으로 최초 1회 생성"(`load_or_seed`) → "DB에 먼저 쓰고 Temporal에
반영"(`save_and_push`)을 담당해, `.env`는 이제 최초 배포 시드로만 남는다. `schedule.py`의
`build_*_schedule`/`ensure_*_schedule`는 `Settings` 의존을 걷어내고 `cron: str`/`count: int`
같은 순수 인자만 받도록 리팩터했다. 채팅 도구가 하나 늘었다 — `set_schedule_time(target,
hour, minute, count)`. 이 작업 중 라이브로 실측한 버그 둘: (1) `ScheduleDescription`이
돌려주는 `spec.cron_expressions`는 서버가 내부 캘린더 스펙으로 컴파일하며 비워버려서(첫
구현에서 `.env` 값을 대신 보여주는 우회로 넘어갔던 지점인데, DB 이관으로 그 우회 자체가
자연스러운 설계가 됐다), (2) `ScheduleHandle.update()`에 넘기는 `Schedule` 객체의 `state.paused`
기본값이 `False`라 시각/건수만 바꿔도 꺼둔 스케줄이 조용히 다시 켜지는 문제 — `_create_or_update`
가 update 전 paused 여부를 읽어두고 필요하면 update 뒤에 다시 pause() 하는 것으로 고쳤다
(`test_ensure_update_preserves_paused_state`, 실제 Temporal로 검증). 구현·contract test(3
백엔드)·Alembic 마이그레이션 upgrade/downgrade 왕복·유닛/통합 테스트·`make check` 통과 완료.
자세한 설계는 ARCHITECTURE.md §11.2f(갱신본).

**승인 대기 중인 지원 건 일괄 재전송** (2026-08-23) — 텔레그램 리스너가 SIGTERM으로 죽었다
재기동된 직후 세션에서 나온 요청. 기존 `resend_pending_decision` 도구는 application_id를
미리 알아야 했는데, 리스너가 잠깐 꺼져 있던 동안 눌렸을 버튼을 재기동 후 복구하려는 상황에선
애초에 어떤 지원 건이 대기 중인지부터 모른다는 게 갭이었다. 새 `pending_decisions.py`(운영
진입점, 새 port 없음 — `cli.py`/`watchdog.py`와 같은 계층)의 `find_pending_decisions`가
Temporal visibility API(`WorkflowType = 'ApplicationWorkflow' AND ExecutionStatus =
'Running'`)로 실행 중인 지원 워크플로우를 전부 훑어 각각의 `pending_decision` query가
`has_pending`인 것만 추리고, `resend_all`이 그걸 전부 재전송한다 — watchdog.py가 *닫힌*
워크플로우를 찾는 것과 반대로 이건 아직 RUNNING인 것 중에서 고른다. `resend_decision`은
Notifier port 표면에 없는 TelegramNotifier 전용 메서드라(§11.6) 여기서도
`telegram/bridge.py`의 `_RevisableNotifier`와 같은 구조적 Protocol(`ResendableNotifier`)로만
가리킨다. `cli.py`의 `resend-pending` 명령(NOTIFIER=telegram 아니면 안내만 하고 끝)과 텔레그램
채팅 도구 `resend_all_pending_decisions`(기존 단일 재전송 도구도 함께
`telegram/_agent_tools_resend.py`로 옮김 — `_agent_tools.py`가 200줄을 넘어서)가 같은 로직을
공유한다. 유닛 테스트(fake client) + `WorkflowType`/`ExecutionStatus` visibility 필터 자체가
실제로 동작하는지 확인하는 실제 Temporal 서버 통합 테스트(watchdog.py의 실측 테스트와 같은
이유로 `start_local()`, `@pytest.mark.temporal`)까지 구현·`make check` 통과 완료.

**application_id만으로 재시도 + 목록에 회사/직무 표시** (2026-08-23) — claude CLI 한도초과로
NEEDS_HUMAN 떨어진 지원 건을, 한도 해결(계정 전환) 뒤 텔레그램에서 재시도하려던 실제 사용
중 발견한 갭. `list_applications`/`get_application`이 canonical_key 해시(application_id)만
보여줘서 어떤 공고인지 알 방법이 없었고, `apply_by_url`은 URL을 알아야만 재시도가 되는데
해시만 봐서는 원 공고 링크를 되찾을 수 없었다(공고 수집 캐시가 24시간 TTL로 걸러지는 건
`start_actionable_applications`의 자동 선정 로직뿐이고 `jobs` 테이블 자체엔 TTL이 없다는
걸 확인 — `apply_intake.find_job_by_application_id`가 그 저장소를 canonical_key로
역스캔한다, 별도 인덱스 없이 선형 스캔이지만 채팅에서 사람이 직접 트리거하는 저빈도
호출이라 감수). 새 `apply_intake.retry_application(application_id)`가 이 역조회로 찾은
URL을 `apply_by_url`과 같은 시작 경로(`_start_workflow`, ALLOW_DUPLICATE)에 그대로
태운다 — 캐시에서 못 찾으면(오래돼 다른 공고로 덮어써짐 등) `apply_by_url`로 URL을 직접
달라고 안내한다(그 경우까지 구제하려면 application 레코드 자체에 job_url을 영속해야 해서
스키마 변경이 필요해지는데, 이번 요청 범위를 넘어선다고 판단해 보류). 텔레그램 채팅 도구
`retry_application`(`telegram/_agent_tools_retry.py`, `_agent_tools_resend.py`와 같은
분리 이유)으로 노출했고, `list_applications`도 같은 job 캐시에서 회사/직무를 찾아 붙이도록
바꿨다(캐시에 없으면 기존처럼 application_id만). 실제 최종 제출은 이 경로로 재시작된
워크플로우 안에서도 여전히 사람의 텔레그램 승인 뒤에만 일어난다. 구현·유닛 테스트·
`make check` 통과 완료.
