# Ports & Adapters — 교체 가능성 설계

> `docs/ARCHITECTURE.md` 색인의 §11.1–§11.2b, §11.3–§11.7. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 11. Ports & Adapters — 교체 가능성 설계

### 11.1 원칙 3개
1. **의존성 역전은 "밖으로 나가는 것"에만.** LLM·S3·DB·Telegram·브라우저 = 전부 port.
   도메인 로직(자격 판정, 상태 전이, Recipe 정책)은 어떤 어댑터도 import 하지 않는다.
2. **Protocol을 쓰고 ABC 상속을 강요하지 않는다.** 구현체가 우리 인터페이스를 상속해야 한다면,
   그 구현체는 이미 우리 코드에 묶인 것이다. `typing.Protocol`은 구조적 타이핑이라 어댑터가 port를 모른다.
3. **교체 가능성은 contract test로만 증명된다.** 인터페이스만 있고 구현이 하나면 그건 교체 가능한 게 아니라
   "교체 가능해 보이는" 것이다. 모든 port는 **구현 2개 이상**(실제 + 테스트 대역)을 갖는다.

### 11.2 Port 목록

| Port | M0 구현 | 대역/2번째 구현 | 교체 가치 | 비고 |
|---|---|---|---|---|
| `LLMClient` | `AnthropicLLM`(M3) · `ClaudeCodeCliLLM`(M3 연장) | `StubLLM`(고정 응답 재생) | **높음** | 모델 교체·비용 실험·테스트 결정성. `ClaudeCodeCliLLM`은 `ANTHROPIC_API_KEY` 종량제 대신 로컬에 로그인된 Claude Code 구독으로 `claude` CLI 를 headless 로 호출한다(§11.2c) |
| `BlobStore` | `S3BlobStore`(MinIO) | `LocalBlobStore` · `InMemoryBlobStore` | **높음** | 로컬 개발에서 컨테이너 하나 덜 띄움. MinIO 는 virtual-hosted-style DNS 를 못 풀어 `addressing_style="path"` 고정 |
| `Notifier` | Telegram | `ConsoleNotifier` | **높음** | 승인 흐름 테스트가 봇 없이 가능 |
| `*Repository` + `UnitOfWork` | SQLAlchemy/Postgres | `FileUnitOfWork` · `InMemoryUnitOfWork` | 중간 | 실제 목적은 DB 교체보다 **테스트 속도**. sub-repo 4개: applications / jobs / attempts / schedule_config |
| `RecipeExecutor` | Playwright | `ReplayExecutor`(고정 HTML) · `AgentBrowserExecutor`(agent-browser CLI, §3) | **높음** | dry_run/supervised/live는 이 port의 모드. `EXECUTOR=playwright`\|`agent_browser`\|`replay` |
| `PlatformAdapter` | `WantedPlatformAdapter` · `SaraminPlatformAdapter` | `FixturePlatformAdapter` | **높음** | 확장 지점. `StaticPlatformRegistry` allowlist 로 등록 |
| `JobSource` | wanted/saramin/jasoseol | `FixtureJobSource` | **높음** | 공고 대량 수집. §11.2b |
| `MatchingConfigSource` | `YamlMatchingConfigSource` | `StaticMatchingConfigSource` | 중간 | 하드컷/트랙 규칙. §11.2b |
| `FactSource` | `YamlFactSource` | `StaticFactSource` | 중간 | 이력서 생성의 유일한 사실 원천(§2.3). `MatchingConfigSource`와 동일 패턴 |
| `ProfileSource` | `YamlProfileSource` | `StaticProfileSource` | 중간 | 이력서 헤더/학력/스킬태그/언어(§2.3). `FactSource`와 동일 패턴 |
| `ResumeGenerator` / `ResumeReviewer` | plain 함수(`SimpleResume*`) | — (§9.2 보류, 아직 2번째 구현 없음) | **높음** | LangGraph/PydanticAI 등, 프레임워크는 미정 — §9.2 보류 결정을 가능하게 하는 seam |
| `Clock` / `IdGen` | 시스템 | 고정값(테스트에서 인라인) | 중간 | 테스트 결정성 |
| `RecipeSource` | `JsonFileRecipeSource`(`var/recipes/{platform}/{version}.json`) | `InMemoryRecipeSource` | **높음** | 버전 목록/승격 invariant(candidate 만 승격, 승격 시 기존 active → deprecated)를 port 가 직접 강제한다 — `/recipes` 라우터(§7)는 그걸 얇게 노출만 |
| `GuideSource` | `FileGuideSource`(`config/resume_guide.{platform}.md`) | `StaticGuideSource` | 중간 | REVISE(general)가 patch 로 갱신하는 생성 가이드(§2.2). `FactSource` 와 같은 캐시-없이-매번-읽기 패턴 |
| `PortfolioSource` | `YamlPortfolioSource`(`config/portfolio_map.yaml`) | `StaticPortfolioSource` | 낮음 | 직무 카테고리 → 포트폴리오 파일 매핑(§2.3) |
| `CredentialSource` | `JsonQueueCredentialSource` | `StaticCredentialSource` | 중간 | **activity 경계를 절대 넘지 않는다**(§2.4b) — 반환값이 Temporal event history 에 영구 기록되므로 `contracts/` 가 아니라 `ports/` 에 두고 구현체 내부에서만 쓴다 |
| `WebAgentExecutor` | `AsideCliExecutor` | `ReplayWebAgentExecutor` | — | 외부 ATS/자체구축 폼(§2.4b). port·adapter·contract test 까지만 있고 실행 흐름에 **미배선, 동결** |
| `PdfRenderer` | `WeasyPrintPdfRenderer`(구현 완료, §2.3) | `StubPdfRenderer`(JSON 덤프) | 낮음 | 교체 가능성보다 격리 목적. weasyprint 렌더 테스트는 시스템 라이브러리 필요해 `native` |
| `AttachmentManager` | `WantedAttachmentManager` | `FixtureAttachmentManager` | 낮음 | `PlatformAdapter`와 별개 축(§11.2e) — 계정에 쌓인 첨부파일 관리. `resume_cleanup.py` 전용 |
| `CheckpointStore` | `FileCheckpointStore` | `InMemoryCheckpointStore` | 중간 | SUPERVISED 페이지 경계 체크포인트 승인/거절(§2.4c) — nonce처럼 프로세스 경계를 넘지만, 워크플로우가 아니라 activity가 기다린다는 점이 다르다 |

```python
# ports/llm.py — 구현을 전혀 모른다
class LLMClient(Protocol):
    async def complete(self, prompt: Prompt, *, max_tokens: int) -> str: ...
    async def structured[T: BaseModel](self, prompt: Prompt, schema: type[T]) -> T: ...

# ports/storage.py
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def presign(self, key: str, ttl: timedelta) -> str: ...

# ports/notifier.py — "Telegram"이라는 단어가 없다
class Notifier(Protocol):
    async def request_decision(self, req: DecisionRequest) -> DecisionTicket: ...
    async def notify(self, event: NotifyEvent) -> None: ...

# ports/executor.py — 브라우저가 아니라 "Recipe를 실행하는 것"을 추상화
class RecipeExecutor(Protocol):
    async def run(self, recipe: AutomationRecipe, ctx: ExecutionContext,
                  mode: ExecutionMode) -> ExecutionResult: ...

# ports/repository.py
class UnitOfWork(Protocol):
    applications: "ApplicationRepository"
    recipes: "RecipeRepository"
    async def __aenter__(self) -> "UnitOfWork": ...
    async def commit(self) -> None: ...
```

**`RecipeExecutor`를 port로 잡고 `BrowserDriver`는 잡지 않는 이유:** Playwright가 이미 Chromium/Firefox/WebKit을
추상화한다. 그 위에 또 한 겹을 두면 Playwright의 표현력만 잃는다. 우리에게 의미 있는 교체 축은
브라우저 엔진이 아니라 **"실제로 제출하는가"** (live / supervised / dry_run / replay)이므로, 그쪽을 port로 잡는다.

### 11.2b 공고 수집·매칭 — `JobSource` vs `PlatformAdapter`, 그리고 순수 domain

ports/domain/adapters 를 먼저 넣고, 그 위에 `JobCollectionWorkflow` + activity + `jobs`
저장소를 얹었다 (Temporal Schedule 배선은 아직 — §2.1 참고). 이전 auto-apply(v1)를 참고했지만
그대로 옮기지 않았다 — v1은 "플랫폼별 수집"이 어댑터 하나 안에서 수집·판정·저장까지 다 하는
구조라 유지보수가 힘들었다. 갈라낸 경계는 세 개다.

1. **`JobSource`(대량 수집) ≠ `PlatformAdapter`(URL 단건 조회).** 이름이 비슷해 헷갈리기 쉽지만
   축이 다르다. `PlatformAdapter.fetch_job(url)`은 사람이 이미 링크를 아는 상태(`ApplicationWorkflow`
   시작)에 쓰고, `JobSource.list_jobs()`는 아직 뭐가 있는지 모를 때 목록을 훑는다. 둘을 하나로
   합치고 싶은 유혹이 있지만, 지금 합치면 "URL 하나 조회"와 "수백 건 순회"가 같은 인터페이스에
   얽혀 어느 쪽 계약도 깔끔하지 않다. 실제 어댑터(예: wanted)가 내부적으로 같은 상세 API를 쓰는 건
   괜찮다 — 인터페이스가 같아야 한다는 뜻은 아니다.
2. **매칭(하드컷/트랙/스코어링/지원가능성)은 domain의 순수 함수다.** `domain/job_screening.py`,
   `domain/job_applicability.py`는 플랫폼을 모른다 — `JobPosting`(contracts/job.py)만 받는다.
   v1은 이 판정을 어댑터 근처에 흩어두지 않고 이미 분리해뒀던 부분이라 구조는 그대로 가져왔다.
   다만 v1의 `evaluate_applicability`는 함수 안에서 레시피 파일을 직접 읽었는데(§11.1 원칙 위반),
   여기서는 `recipe_exists`/`form_has_essays`/`session_ok`/`required_gaps`를 호출부(미래의 activity)가
   미리 확인해 인자로 넘긴다 — domain은 파일도 DB도 모른다.
3. **매칭 규칙(키워드/트랙/하드컷)은 `MatchingConfigSource` port 뒤의 데이터다.** `config/matching.yaml`은
   Recipe와 같은 이유로 코드가 아니라 데이터다 — 이 프로젝트 사용자의 직무 취향이 바뀌면 코드를
   고치지 않고 이 파일만 고친다. v1의 값(하드컷/트랙/스코어링 키워드)을 그대로 옮겼다 — 같은
   사용자의 실제 구직 취향이라 새로 지어낼 이유가 없었지만, **로직 구조**(파일 하나에서 플랫폼별로
   분기하던 v1의 실수)는 반복하지 않았다.

```python
# ports/job_source.py
class JobSource(Protocol):
    @property
    def platform(self) -> str: ...
    def list_jobs(self) -> AsyncIterator[JobPosting]: ...
    async def enrich(self, job: JobPosting) -> JobPosting: ...

# ports/matching_config.py
class MatchingConfigSource(Protocol):
    async def load(self) -> MatchingConfig: ...
```

새 플랫폼을 추가하는 절차는 §11.2 원칙 그대로다: `adapters/job_source/`에 클래스 하나 추가하고
`bootstrap.py`의 `_build_job_sources`에 등록하면, `domain/job_screening.py` 이하는 손대지 않는다.

`JobCollectionWorkflow`(§2)는 플랫폼별로 `collect_platform_jobs` activity 하나를 동시에 돌린다
(`asyncio.gather` — 한 플랫폼이 느리거나 실패해도 나머지 결과를 지우지 않는다). 그 activity
하나가 목록 수집 → 스크리닝 → 상세 조회 → 재스크리닝 → 지원가능성 판정 → `jobs` 저장까지
전부 담당한다(`activities/job_collection.py`) — 공고 하나씩 별도 activity로 쪼개지 않는 이유는
플랫폼당 수백 건을 개별 activity 스케줄링하면 Temporal 히스토리만 커지고 얻는 게 없어서다
(재시도 단위는 "이 플랫폼 전체"로 충분하다 — 부분 실패는 activity 안에서 흡수한다:
상세 조회 한 건이 실패해도 나머지는 계속 처리하고 `enrich_errors`로만 센다).

`jobs` 저장은 `applications`와 같은 `UnitOfWork`(§4.1) 뒤에 있다 — `JobRepository.upsert()`는
`(platform, platform_job_id)` 기준 멱등이라 재수집이 행을 늘리지 않는다. M1 은 파일 기반
(`FileJobRepository`)이고, §4의 `jobs` 테이블(Postgres)은 M2 에서 같은 port 로 교체한다.

Schedule(cron) 등록은 `schedule.py`의 `ensure_job_collection_schedule`/`delete_job_collection_schedule`
+ `cli.py collect-schedule`/`collect-unschedule`로 배선했다. `ensure_job_collection_schedule`는
create-or-update다 — `client.create_schedule()`이 `ScheduleAlreadyRunningError`를 던지면(이미
등록돼 있으면) `ScheduleHandle.update()`로 덮어쓴다. 몇 번을 실행해도 최종 상태가 같아서(idempotent),
배포 스크립트가 매번 무조건 호출해도 안전하다. cron 표현식과 플랫폼 목록은 `schedule.py` 자신은
모른다(순수하게 인자로 받는다) — `schedule_config.py`가 DB(`ScheduleConfig`, §11.2f) 또는
`.env`(DB가 비어 있을 때의 최초 시드)에서 읽어 넘긴다. 겹쳐 도는 걸 막기 위해
`SchedulePolicy(overlap=SKIP)`을 쓴다(재시도 단위가 "플랫폼 전체"라 겹쳐 돌면 같은 공고를 두
activity가 동시에 upsert할 수 있어서다 — `JobRepository.upsert()`가 멱등이라 깨지진 않지만
막을 이유가 있다).

수동 1회 실행(`uv run python -m auto_apply.cli collect --platforms wanted,saramin`)은 여전히
유효하다 — Schedule은 "누가/언제 시작하는지"만 바꾸고 워크플로우 자체는 그대로다.

**테스트 함정:** Temporal의 time-skipping test server(`WorkflowEnvironment.start_time_skipping()`,
§ test_ping.py)는 `CreateSchedule` RPC를 구현하지 않는다(`RPCError: ... is unimplemented`) — 이
프로젝트의 다른 workflow 통합 테스트가 쓰는 서버가 이거다. 그래서 Schedule RPC를 실제로 검증하려면
`WorkflowEnvironment.start_local()`(풀 dev server, 최초 실행 시 별도 바이너리 다운로드)을 써야
한다(`tests/test_schedule.py`). `build_job_collection_schedule()` 자체는 순수 함수라 서버 없이도
바로 테스트한다 — Temporal 연동이 필요한 부분(`ensure_*`/`delete_*`)만 얇게 분리해둔 이유다.

> **§11.2c–§11.2f는 [`11-2-operational-entrypoints.md`](11-2-operational-entrypoints.md)로 옮겼다** —
> `ClaudeCodeCliLLM`(§11.2c) · watchdog/알림 사각지대(§11.2d) · 첨부파일 정리(§11.2e) ·
> 자동 지원 Schedule(§11.2f) · 승인 대기 일괄 재전송(§11.2g).

### 11.3 Temporal에서의 주입 — activity가 곧 seam

이 부분이 Temporal 프로젝트에서 가장 자주 틀리는 지점이다.

- **Workflow는 의존성을 가질 수 없다.** 결정적이어야 하고, Temporal의 workflow sandbox가 모듈을 재import한다.
  → workflow 파일은 `contracts/`(stdlib + dataclass)만 import 한다. 어댑터를 import 하면 sandbox 경고/오류가 난다.
- **Activity는 클래스로 만들고 생성자에서 port를 주입한다.** 그리고 worker에 *바인딩된 메서드*를 등록한다.

```python
# contracts/activity_defs.py  — 인터페이스 stub. 절대 worker에 등록하지 않는다
@activity.defn(name="generate_resume")
async def generate_resume(req: GenerateResumeRequest) -> ResumeDraft:
    raise NotImplementedError("interface only")

# activities/resume.py — 구현. 이름이 stub과 같다
class ResumeActivities:
    def __init__(self, generator: ResumeGenerator, reviewer: ResumeReviewer,
                 store: BlobStore, uow: Callable[[], UnitOfWork]) -> None:
        self._gen, self._rev, self._store, self._uow = generator, reviewer, store, uow

    @activity.defn(name="generate_resume")
    async def generate_resume(self, req: GenerateResumeRequest) -> ResumeDraft:
        draft = await self._gen.generate(req)
        await self._store.put(f"ai-traces/{activity.info().workflow_id}/draft.json", ...)
        return draft

# workflows/resume.py — stub을 호출한다. 타입 체크는 되고, 구현은 모른다
from auto_apply.contracts.activity_defs import generate_resume
draft = await workflow.execute_activity(generate_resume, req,
                                        start_to_close_timeout=timedelta(minutes=5))
```

동작 원리: `execute_activity(stub, ...)`는 `@activity.defn`의 **이름**("generate_resume")을 서버에 보내고,
worker는 같은 이름으로 등록된 구현을 실행한다. 그래서 **workflow → activity 구현 방향의 import가 0**이 된다.
(주의: stub 함수를 worker의 `activities=[...]`에 넣으면 안 된다. 넣으면 `NotImplementedError`가 난다.)

### 11.4 Composition root — 조립은 한 곳에서만

```python
# bootstrap.py — 구현체 선택이 존재하는 유일한 파일
def build_container(cfg: Settings) -> Container:
    llm: LLMClient = _build_llm(cfg)                     # stub | anthropic | claude_cli
    # 도구 선택/응답 판단은 이력서 생성보다 훨씬 가벼운 분류라 같은 프로바이더의 더 싼 모델로
    # 인스턴스를 하나 더 만든다 (§6, TELEGRAM_AGENT_MODEL)
    chat_llm: LLMClient = _build_llm(cfg, model=cfg.telegram_agent_model)
    store: BlobStore = _build_store(cfg)                 # local | memory | s3
    notifier: Notifier = TelegramNotifier(cfg.telegram) if cfg.notifier == "telegram" else ConsoleNotifier()
    generator: ResumeGenerator = SimpleResumeGenerator(llm, ...)   # §9.2 — 프레임워크 미도입
    return Container(llm=llm, chat_llm=chat_llm, store=store, notifier=notifier, ...)

# worker.py
c = build_container(settings)
acts = [*ResumeActivities(c.generator, c.reviewer, c.store, c.uow).all(),
        *BrowserActivities(c.executor, c.store, c.uow).all()]
Worker(client, task_queue=queue, workflows=[ApplicationWorkflow, ...], activities=acts)
```

- 환경변수 하나가 port 하나의 구현을 고른다(`config.py`의 `Literal` 이 선택지의 단일 출처다):
  `LLM_PROVIDER` · `STORAGE` · `NOTIFIER` · `EXECUTOR` · `REPOSITORY` · `JOB_SOURCE` ·
  `PDF_RENDERER` · `CHECKPOINT_STORE` · `MATCHING_CONFIG` · `FACTS_SOURCE` ·
  `PROFILE_SOURCE` · `PORTFOLIO_SOURCE` · `GUIDE_SOURCE` · `WEB_AGENT` · `CREDENTIAL_SOURCE` ·
  `RESUME_ENGINE`(`langgraph` 선택지는 자리만 있고 구현이 없다 — §9.2).
  덕분에 **`EXECUTOR=replay NOTIFIER=console LLM_PROVIDER=stub REPOSITORY=memory STORAGE=memory`로
  전체 파이프라인을 오프라인 실행**할 수 있다. M2 이후 이게 개발 속도를 가장 크게 좌우한다.
- DI 프레임워크는 쓰지 않는다. 명시적 생성자 주입 + 파일 하나로 충분하고, 그게 더 읽기 쉽다.
- FastAPI는 `Depends`로 컨테이너에서 꺼내 쓰기만 한다. 라우터가 어댑터를 직접 생성하지 않는다.

### 11.5 Contract test — 교체 가능성의 유일한 증거

```python
# tests/ports/test_blobstore.py
@pytest.fixture(params=["s3", "local"])
def store(request, tmp_path, minio) -> BlobStore:
    return S3BlobStore(minio) if request.param == "s3" else LocalBlobStore(tmp_path)

async def test_put_then_get_roundtrip(store: BlobStore): ...
async def test_get_missing_raises_blob_not_found(store: BlobStore): ...
async def test_presign_url_is_fetchable(store: BlobStore): ...
```

같은 스위트를 모든 구현에 돌린다. 새 어댑터를 추가할 때 할 일은 **params에 이름 하나 추가**뿐이고,
그 순간 이미 정의된 계약 전체가 강제된다. 특히 `LLMClient.structured()`(스키마 위반 시 어떤 예외?)와
`RecipeExecutor`(`AlreadySubmitted`를 어떻게 보고?)의 **예외 계약**을 여기서 못박는다 —
port에서 새는 것은 보통 반환값이 아니라 예외다.

### 11.6 추상화하지 말아야 할 것 (의도적 결정)

| 대상 | 왜 추상화하지 않는가 |
|---|---|
| **Temporal** | 워크플로우 엔진을 감싸면 durable timer·signal·history라는 도입 이유 자체를 잃는다. Temporal은 프레임워크로 받아들이고, 대신 **도메인 로직을 activity 안에서 얇게** 유지해서 엔진과 무관한 부분을 순수 함수로 뽑는다. |
| **Pydantic / SQLAlchemy 타입** | 데이터 계약 도구를 한 겹 더 감싸면 검증 표현력이 사라진다. 다만 **SQLAlchemy 모델을 도메인 객체로 API 밖까지 흘리지 않는다** (Repository가 경계에서 DTO로 변환). |
| **브라우저 엔진** | §11.2 참고. Playwright가 이미 그 역할이다. |
| **Telegram 메시지 포맷** | `Notifier` port는 "결정을 요청한다"는 의도만 추상화한다. 버튼/마크다운 같은 채널 고유 표현은 어댑터 안에 둔다. port가 채널 UI를 알기 시작하면 그건 이미 Telegram 전용 인터페이스다. |

### 11.7 체크리스트 (PR 리뷰 기준)
- [ ] `domain/`과 `ports/`가 `adapters/`를 import 하지 않는다 — CI에서 import-linter로 강제
- [ ] `workflows/`가 `contracts/` 외의 내부 모듈을 import 하지 않는다
- [ ] 새 외부 의존성을 넣을 때 port를 먼저 정의했다
- [ ] 모든 port에 구현이 2개 이상 (실제 + 테스트 대역)
- [ ] 어댑터 생성 코드가 `bootstrap.py` 밖에 없다 — **운영 진입점 계층**(`telegram/`·`cli`·
      `watchdog`·`worker`·`schedule*`·`apply_intake`·`pending_decisions`·`process_alerts`·
      `resume_cleanup`)도
      포함이다. 이 층은 원래 어떤 계약에도 안 걸려 있어서, 규칙이 강한 계층을 피해 새 기능이
      전부 이쪽으로 흘러드는 압력이 있었다(채팅 도구·스케줄·자동 지원이 실제로 그렇게 자랐다).
      새 규칙을 만드는 대신 이 계약 하나만 넓혀 걸었다(2026-08-23) — 나머지는 자유다:
      Temporal Client SDK 직접 사용도, `workflows`/`activities` import도 정상이다.
- [ ] port 시그니처에 벤더 타입이 노출되지 않는다 (`boto3` 객체, Anthropic `Message` 등)
- [ ] 테스트 마커가 "무엇이 있어야 도는가"와 맞다 — `docker`(make up) / `native`(브라우저·시스템
      라이브러리) / `temporal`(테스트 서버, Docker 불필요라 `make check`에 포함) / 무마커(기본)
