# auto-apply v2

구직 지원 자동화. **Temporal**이 워크플로우/상태를, **AI**가 이력서와 Recipe를, **Playwright**가
검증된 Recipe 실행을 담당한다. 사람은 **Telegram**으로 승인한다.

> 작업 시작 전 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)를 읽어라. 아래는 그 문서의 실행 규칙 요약이며,
> 충돌하면 ARCHITECTURE.md가 기준이다. 설계를 바꿀 때는 코드와 그 문서를 같이 수정한다.

## 명령어

```bash
make setup     # uv sync + .env 생성
make up        # 인프라 기동 (postgres/temporal/temporal-ui/minio) → UI: localhost:8080
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

## 코드 컨벤션

- **절대 import만** (`from auto_apply.x import y`). 상대 import는 ruff TID252가 막는다.
- DTO는 `extra="forbid"` + `frozen=True`. LLM이 만든 필드가 실행 계층까지 흐르지 않게 한다.
- 열거형은 `StrEnum` (Temporal payload·DB 저장 모두 안전).
- 로그는 structlog, **모든 로그에 `workflow_id`를 구조화 필드로** 넣는다. DB·S3·트레이스를 잇는 유일한 키다.
- 주석은 "왜"만 쓴다. 설계 근거는 `ARCHITECTURE.md §N`으로 참조한다.

## 하지 말 것

- CAPTCHA 우회/자동 해결 — `CaptchaEncountered`를 던지고 사람에게 넘긴다.
- 비밀번호를 코드가 타이핑하지 않는다. 사용자가 수동 로그인한 `storage_state`를 재사용한다.
- `DRY_RUN_ONLY`를 사용자 확인 없이 끄지 않는다.
- AI가 만든 Recipe를 `active`로 바로 올리지 않는다 (`draft → candidate → active`, 사람 승격).
- `applications.status`를 `persist_state` 밖에서 UPDATE하지 않는다.
- 플랫폼 rate limit(`platform_policies`)을 우회하는 코드를 추가하지 않는다.

## 현재 상태

**M0 완료** — 스캐폴딩/툴체인/계층 가드/contract test 패턴 + `PingWorkflow` 스모크(통합 테스트 통과).

아직 **없는** 것: DB 모델·Alembic 마이그레이션, `ApplicationWorkflow`, Telegram 어댑터,
Playwright executor, Anthropic 어댑터, S3 어댑터, LangGraph(M3에서 판단).

**다음: M1 — Telegram 승인 → signal → durable timer.**
완료 기준은 "코드가 돌아간다"가 아니라 **"워커를 강제 종료해도 예약이 살아있다"**다.
