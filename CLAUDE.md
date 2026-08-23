# auto-apply v2

구직 지원 자동화. **Temporal**이 워크플로우/상태를, **AI**가 이력서와 Recipe를, **Playwright**가
검증된 Recipe 실행을 담당한다. 사람은 **Telegram**으로 승인한다.

> 작업 시작 전 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)를 읽어라 — **색인**이다. 본문은
> `docs/architecture/` 아래에 절(§N) 단위로 나뉘어 있고, 색인 표에서 필요한 절만 골라 읽는다.
> 아래는 그 문서의 실행 규칙 요약이며, 충돌하면 ARCHITECTURE.md 쪽이 기준이다.
> **절 번호는 코드 주석·테스트가 참조하므로 파일이 갈려도 바뀌지 않는다.**
> 설계를 바꿀 때는 코드와 해당 절 파일을 같은 커밋에서 수정한다.

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
운영 진입점  telegram/·cli·watchdog·worker·schedule*·apply_intake·pending_decisions·
            process_alerts·resume_cleanup — Temporal Client SDK 를 직접 써도 되고 workflows 를 import 해도
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
- 설계가 바뀌면 `docs/architecture/`의 해당 절 파일을 같은 커밋에서 수정한다. 새 절을 만들면
  `docs/ARCHITECTURE.md` 색인 표에도 줄을 추가한다 — 색인에 없는 파일은 없는 것과 같다.
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

**M0~M4 완료, M5(다중 플랫폼) 진행 중.** 마일스톤별 완료 기준과 "마일스톤 밖에서 자란 것",
그리고 **아직 안 한 것**은 §10에 있다. 아래는 어떤 기능이 어느 절에 문서화돼 있는지의 색인이다
— 설계 근거·실측 기록·기각한 대안은 전부 그쪽에 있고, 여기 요약을 보고 판단하지 말고
해당 절을 읽어라.

| 기능 | 상태 | 설계 근거 |
|---|---|---|
| 승인 흐름 · durable timer · nonce | 완료 | §2.2, §6 |
| REVISE 3갈래(specific/general) · 가이드 patch 2차 승인 | 완료 | §2.2, §6 |
| Fact 기반 이력서 생성 · `ground_check` · 블록 구조 · WeasyPrint PDF | 완료 | §2.3 |
| Playwright / agent-browser executor · Recipe 데이터 모델 | 완료 | §3 |
| `application_attempts` 감사 로그 · 부분 제출 방어 · `verify_submission` | 완료 | §5 |
| Postgres/Alembic (`REPOSITORY=postgres`) · S3BlobStore | 완료 | §9.1, §11.2 |
| `AutomationRepairWorkflow` 전 구간 · goto timeout 자동 수선 | 완료 | §2.4 |
| SUPERVISED 페이지 경계 체크포인트 (`CheckpointWaiter`) | 완료 | §2.4c |
| 공고 수집·매칭 · `JobCollectionWorkflow` · Schedule | 완료 | §11.2b |
| `ClaudeCodeCliLLM`(로컬 구독) · 프롬프트 캐시 · 장애 알림 | 완료 | §11.2c |
| watchdog · 알림 사각지대 4종 · 프로세스 크래시 알림 | 완료 | §11.2d |
| 첨부파일 정리 (`AttachmentManager` / `resume_cleanup.py`) | 완료 | §11.2e |
| 자동 지원 Schedule · 시각/건수 DB 저장 · 텔레그램 관리 | 완료 | §11.2f |
| 승인 대기 일괄 재전송 (`pending_decisions.py`) | 완료 | §11.2g |
| 텔레그램 자유 텍스트 채팅 에이전트 (도구 12개) | 완료 | §6 |
| `/applications` · `/recipes` REST 표면 | 완료 | §7 |
| 외부 ATS `WebAgentExecutor`(Aside) | **동결** — port·adapter·contract test 까지만, 미배선 | §2.4b |
| OTel exporter 실제 연결 | **미완** — 자리만 있다 | §9.4 |
| 자소서 답변 생성 파이프라인 | **없음** | §2.4b |
| `ResumeReviewer` 2번째 구현 | **의도적 미충족** — LLM 2차 리뷰를 안 넣기로 한 결정과 묶임 | §2.3, §11.1 |

**플랫폼별 진척**: wanted 는 수집 → 판정 → 이력서 → 승인 → 실행 → 제출 검증까지 실계정
end-to-end 검증 완료. saramin 은 `JobSource`/`PlatformAdapter`/`verify_submission`/draft
recipe 까지 있고 **자소서 문항 있는 공고가 미검증**이라, 텔레그램 `apply_by_url`은 wanted 로만
제한돼 있다(§6). jasoseol 은 `JobSource` 만 있다.


## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
