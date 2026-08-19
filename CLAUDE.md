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
make check     # lint + type + arch + test  ← 커밋 전 필수
make test      # 단위/계약 테스트 (인프라 불필요)
make test-all  # 통합 테스트 포함
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
- 테스트가 인프라를 필요로 하면 `@pytest.mark.integration`을 붙인다. 기본 `make test`는
  Docker 없이 항상 돌아야 한다.

**커밋 규칙 (개발 초기 단계)**

- **큰 기능 단위로 하나씩 커밋한다.** (예: "승인 흐름 + durable timer", "Playwright executor")
  여러 기능을 한 커밋에 몰지 않고, 반대로 파일 하나 고칠 때마다 커밋하지도 않는다.
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

- CAPTCHA 우회/자동 해결 — `CaptchaEncountered`를 던지고 사람에게 넘긴다.
- 비밀번호를 코드가 타이핑하지 않는다. 사용자가 수동 로그인한 `storage_state`를 재사용한다.
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

**M2 진행 중** — Playwright executor(`adapters/executor/playwright.py`, `EXECUTOR=playwright`),
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

**M3 진행 중** — `SimpleResumeGenerator`/`SimpleResumeReviewer`가 자리만 잡아둔 상태였던 걸
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
뒤에서 갈아끼울 수 있게만 열어뒀다. `ResumeGenerator`/`ResumeReviewer`는 아직 구현이
`SimpleResume*` 하나뿐이라 §11.1의 "구현 2개" 원칙을 완전히 채우지는 못한 상태다.

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

아직 **없는** 것: S3 어댑터, `AutomationRepairWorkflow`(M4), `/recipes/{platform}` 계열
엔드포인트(승격은 지금은 손으로 recipe JSON의 `status`를 고쳐서 한다).

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
