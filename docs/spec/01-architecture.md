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
  ③ 컨테이너 조립 ④ JobRunner 기동. 종료 시 JobRunner 를 먼저 세우고 브라우저를 닫는다. 바인드는 `127.0.0.1` 고정(옵션 없음),
  `--port 0` 이면 빈 포트를 골라 `auto-apply ready: http://127.0.0.1:<port>` 한 줄을 찍는다. 상태 확인은 `GET /health`.
- BrowserHost(`ports/browser.py`, D6): 기동 시 띄우지 않고 **첫 사용 때** 설치된 Chrome 을 `chrome-profile/` 로 headful
  기동한다 — `channel="chrome"` 우선, 실패하면 mac/Windows 표준 설치 경로를 찾아 `executable_path`, 둘 다 없으면
  `ChromeNotFound`. 사용자 기본 Chrome 프로필(또는 그 안)을 가리키면 기동을 거부한다(`PolicyViolation`).
  프로필은 옆의 `chrome-profile.lock` OS 파일 잠금으로 한 호스트만 쓴다 — 같은 데이터 디렉터리로 두 번째 앱이 뜨면
  브라우저 첫 사용에서 `BrowserProfileInUse`(잠금은 프로세스가 죽으면 OS 가 푼다). 사람이 브라우저를 닫으면 다음 사용 때
  다시 띄운다. 페이지는 불투명 `PageHandle` 로만 밖에 나간다. Ctrl+C 신호는 Playwright 가 아니라 앱이 받아
  lifespan 에서 정상 종료한다(쿠키가 디스크에 써지게).

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
DRAFT ───────────▶ QUEUED ──▶ FILLING ─────────────▶ AWAITING_APPROVAL ──approve──▶ SUBMITTING ──▶ SUBMITTED
                     ▲          │  ▲                    │    ▲     │                   ├──▶ SUBMIT_MISMATCH ──▶ AWAITING_APPROVAL
                     │          ▼  │ answer/login    revise  │   reject                 └──▶ FAILED
                     │   NEEDS_INPUT·NEEDS_LOGIN        ▼    │     ▼
                     │          │                    REVISING ┘   REJECTED
                     └──────────┘ (재진입 run)
FILLING ──(인프라 재시도·크래시 복구)──▶ QUEUED          QUEUED·FILLING·REVISING ──▶ FAILED ──(사람이 다시 시작)──▶ QUEUED
FILLING·REVISING·SUBMITTING ──▶ INCIDENT ──(사람이 사이트에서 확인)──▶ SUBMITTED | CANCELLED   (§A4 L5·제출 중 중단, 자동 재시도 없음)
종결(SUBMITTED·REJECTED·CANCELLED) 을 뺀 어느 상태에서든 cancel ──▶ CANCELLED
```

| 현재 | 갈 수 있는 곳 (+ 종결 외 전부 CANCELLED) |
|---|---|
| DRAFT | QUEUED |
| QUEUED | FILLING · FAILED(핸들러가 시작도 못 함) |
| FILLING | AWAITING_APPROVAL · NEEDS_INPUT · NEEDS_LOGIN · FAILED · INCIDENT · QUEUED(인프라 재시도·크래시 복구) |
| NEEDS_INPUT · NEEDS_LOGIN | FILLING(같은 run 에서 사람이 마침) · QUEUED(대기 끝난 뒤 재진입 run) |
| AWAITING_APPROVAL | SUBMITTING(승인 — 제출 단계로 가는 유일한 간선) · REVISING · REJECTED |
| REVISING | AWAITING_APPROVAL(완료·중단 복구 — 제출 전 L6 대조가 다시 본다) · FAILED · INCIDENT |
| SUBMITTING | SUBMITTED · SUBMIT_MISMATCH · FAILED(최종 클릭 전에 멈춘 게 확실할 때만) · INCIDENT(클릭이 나갔는지 모름 — 크래시·예외) |
| SUBMIT_MISMATCH | AWAITING_APPROVAL |
| FAILED | QUEUED(사람이 다시 시작) |
| INCIDENT | SUBMITTED(사람이 사이트에서 확인) — 그 외엔 CANCELLED 뿐 |
| SUBMITTED · REJECTED · CANCELLED | (없음) |

- 전이 표는 `domain/application_state.py` 에 순수 함수로(`check_transition`). 허용되지 않은 전이는 `InvalidTransition`
  (재시도 없음). 크래시 복구·재시도 간선도 이 표에 있다 — JobRunner(§A9)도 표를 거쳐서만 되돌린다.
- 상태를 쓰는 통로는 `ApplicationService.transition(application_id, to, *, run_id, reason)` **하나**(생성은 `create()` 가
  DRAFT 로, 제출 모드 기본 dry_run). 저장소의 쓰기(`add`·`append_state`)는 이 서비스만 부른다 — import-linter 는 메서드
  호출을 못 봐서 `tests/services/test_state_write_seal.py` 가 src AST 를 훑어 서비스·저장소 구현 밖의 `.applications`·
  `.append_state` 접근을 거부한다. `append_state(state, expected=)` 는 현재 상태가 `expected` 일 때만 쓰는 조건부
  UPDATE 라, 서비스가 읽은 뒤 API·러너가 먼저 전이했으면 `InvalidTransition` 으로 아무것도 쓰지 않는다.
- 이력 행(`PersistState`): `run_id`(사람 조작 전이는 None) · `state` · `reason` · `at` · `submitted_at`.
- `runs` 테이블: 에이전트 세션 1회 = run 1행 (`kind=fill|revise|submit`, `status=running|done|failed|interrupted`,
  `result`(남긴 지원 건 상태), 입력·출력 토큰, 시작·종료 시각, `transcript_path`(DATA_DIR 기준 상대 경로), `error`).
- 스키마(Alembic 이 유일한 원천, `models.py` 와의 일치는 테스트가 대조): `0001`(T0.2) `applications`(지원 건 1행 = 최신 상태
  스냅샷) · `application_state_history`(전이 이력 = 감사 로그, **append-only** — 전이마다 새 행, 자동 증가 `id` 가 순번,
  최신 = 가장 큰 순번. 같은 run 안의 FILLING↔NEEDS_INPUT 왕복도 그대로 쌓인다) · `runs`. `0003`(T3.1) applications 에
  `url`·`domain`(호스트, 도메인 가이드·중복 지원 조회 키)·`submit_mode`(생성 시점 스냅샷, 기본 `dry_run`), 이력 `run_id`
  nullable, runs 에 결과·토큰·transcript. `0004`(T3.2) `jobs`(§A9). 리비전 스크립트는 설치본에도 실리도록 패키지 안
  (`adapters/repository/migrations/`)에 있고 `adapters/repository/migrate.py` 가 ini 없이 Config 를 조립한다
  (루트 `alembic.ini` 는 `alembic revision` 개발용).
- 크래시 복구(`CRASH_RECOVERY`, JobRunner 기동·정지 때 `ApplicationService.recover_interrupted`): `RUNNING` run·job 을
  `INTERRUPTED` 로 닫고 지원 건을 직전 재개 가능 상태로 되돌린다 — FILLING→QUEUED, REVISING→AWAITING_APPROVAL,
  SUBMITTING→**INCIDENT**(최종 클릭이 나갔는지 모른다 — FAILED 는 "제출됐을 수 있음"을 숨긴다. 사람이 사이트를 보고
  SUBMITTED/CANCELLED 로 닫고, 자동으로 다시 제출하지 않는다). 사람 대기(NEEDS_*)는 그대로 둔다.
- 핸들러 실패 → 상태(`failure_target`, 러너가 `ApplicationService.settle_failure` 로): 러너가 정리하는 건
  QUEUED·FILLING·REVISING·SUBMITTING 뿐(그 밖이면 핸들러가 이미 전이를 마쳤다). SUBMITTING 이면 무엇이든 INCIDENT,
  재시도면 `CRASH_RECOVERY` 쪽, NEEDS_LOGIN·NEEDS_INPUT·INCIDENT 는 그 상태(표에 없으면 FAILED), 나머지 FAILED.
  그 사이 다른 쪽(취소 등)이 먼저 바꿨으면 그쪽이 이긴다.

## §A4 제출 차단 하네스 (SubmitGuard) — 제품의 핵심 안전장치

원칙: **LLM 을 신뢰하지 않는다. 차단은 결정적 코드이고, 여러 겹이며, 실패하면 막는 쪽으로 닫힌다.**

| 층 | 무엇을 | 어떻게 |
|---|---|---|
| L1 도구 표면 | 에이전트가 쓸 수 있는 동작 제한 | 임의 JS 실행·Enter 키·좌표 클릭 도구 없음. CLI 경로는 우리 MCP 도구만 허용(`--strict-mcp-config`, 내장 도구 전부 차단) |
| L2 클릭 분류 | 모든 `click(ref)` 전에 대상 분류 | `domain/submit_classifier.py`: `type=submit`, 폼 기본 버튼, 제출 어휘(제출/지원하기/지원 완료/최종/Submit/Apply/Send…), dialog 안의 확인/OK/예 → **risky**. 단, 라벨 전체가 단계 어휘("다음"·Next)인 제출 컨트롤은 마지막 단계 신호가 없으면 **step**(D17). 순수 함수, 픽스처로 회귀 테스트 |
| L3 risky 클릭 격리 | risky 클릭은 **strict 네트워크 모드**에서만 실행 (step 은 step 창 — 아래 D17) | 클릭 창(window) 동안 비-GET 요청(document/xhr/fetch/beacon) 전부 abort + 쿼리·입력값을 싣는 GET 탐색 abort + 페이지 내 `form.submit/requestSubmit`·submit 이벤트 차단(모든 모드). 차단이 발생하면 "이 클릭은 제출 동작이었다"로 판정해 에이전트에게 `SUBMIT_BLOCKED` 반환 → `ready_for_review` 로 유도 |
| L4 네이티브 대화상자 | `confirm()/prompt()/beforeunload` | FILL 단계에선 자동 dismiss(거절). `alert` 만 확인(선택지가 없다). 처리한 대화상자는 도구 결과로 알린다 |
| L5 사후 감지 | 뚫렸는지 확인 | 도구 호출마다 URL/본문에 **새로** 완료 어휘("지원이 완료", "application received"…)가 나타나면 run 즉시 중단 + `INCIDENT` 이벤트 + UI 경고 |
| L6 제출 실행 | 승인 후에만 | SUBMITTING 단계에서도 에이전트는 제출 버튼을 못 누른다. `commit_submit()` 을 부르면 **하네스가** (a) FillLog 필드 값 DOM 재판독·대조 (b) 기록된 제출 대상과 동일 요소인지 확인 (c) `submit_mode=live` 확인 후 직접 클릭. dry_run 이면 클릭 없이 증거만 남김 |

- 비-risky 클릭(`type=button` 다음/Next/저장 후 계속, 파일 업로드 등)은 relaxed 모드 — 단계 저장·업로드 POST 허용.
- L2 는 **허용 목록**이다(T2.2): 위험 신호가 없어도 Safe 근거(입력 요소 · 실제 href 링크 · 안전 어휘 — 한글 음절·ASCII 만인
  라벨)가 있어야 Safe, 나머지는 `UNRECOGNIZED` 로 risky. `type=submit` 은 라벨("다음")보다 우선하고, type 값은 HTML 처럼
  ASCII 대소문자만 무시한다(알 수 없는 button type = submit). 어휘는 `domain/submit_vocabulary.py` 데이터로 분리.
- L5 완료 판정(`detect_completion`)은 폼 안내문("지원이 완료되면 …")·버튼 라벨("지원 완료")에 걸리지 않게 한국어는
  과거형(되었/됐)·감사 인사를, URL 은 호스트를 뺀 경로·쿼리 토큰만 본다. 클릭 전부터 있던 근거인지 가르는 건 SubmitGuard 몫.

**SubmitGuard 설계 (T2.5)** — 규칙은 `domain/submit_guard_policy.py`(순수), 창 운영은 `services/submit_guard.py`,
브라우저 장치는 `adapters/browser/playwright_guard.py`(context 하나당 route·init script·dialog 처리기)·`playwright_guarded.py`.
드라이버 계약은 `ports/browser.GuardedPageDriver`(PageDriver + `arm/disarm/set_window/settle/drain/
describe/click/read_text/submit_target`), 대역은 `adapters/browser/fake_guard.py` + `fake_effects.py`(핸들러를 데이터로).

- **창 모델**: 페이지를 건드리는 도구 호출 하나가 창 하나다(click·check·fill·select·upload·navigate·back·scroll). click·check 는
  L2 분류로 모드를 정하고(요소 + 클릭이 닿는 조작 가능한 조상·라벨의 컨트롤까지 — 하나라도 risky 면 strict, 아니면 하나라도
  step 이면 step), 나머지는 relaxed. 창이 끝나도 모드는 **다음 창이 열릴 때까지 유지**되고(step 창만은 끝나면 relaxed 로
  되돌린다), strict 창 뒤 3초(`STRICT_TAIL_S`) 안에는 strict 가 아닌 창(relaxed·step)을 열지 않고
  기다린다 — risky 클릭 핸들러가 `setTimeout` 으로 미룬 제출이 창 밖이나 이어지는 안전한 동작의 창에 섞여 새지 않게.
  동작이 뜻밖의 예외로 끝나도 꼬리는 걸린다(핸들러는 이미 돌았을 수 있다).
  run 을 끝내며 가드를 끄는 것(`close`)도 꼬리가 지난 뒤다. 동작 뒤에는 최소 0.4초, 그 창에서 시작된 요청이 끝날 때까지
  최대 3초 기다려(`settle`) 핸들러의 비동기 후속을 창 안에 담는다(닫힌 탭의 요청은 끝 이벤트가 없어 셈에서 뺀다).
  창 사이에 막은 것은 다음 도구 결과(`ToolResult.guard`)에 정보로 싣고, 창 안에서 막은 것만 `SUBMIT_BLOCKED` 다.
- **요청 판정**(`request_verdict`): 앱 출처(루프백 별칭·포트) → 항상 차단(가드가 꺼져 있어도, T2.4 이관). 비-GET **문서** 탐색
  (폼 POST)와 입력한 값(3자 이상)이나 주민번호 꼴을 URL 에 싣는 문서 탐색(GET 폼 제출) → 모든 모드에서 차단. relaxed → 그 밖은
  통과(단계 저장·업로드 fetch/xhr/beacon, 입력값을 싣는 자동 완성 요청). strict → 비-GET 전부(알 수 없는 method 포함), 쿼리를
  싣는 문서 탐색(`location.href="/apply?…"`, method=get 폼), 입력값·주민번호 꼴을 URL 에 싣는 GET 요청을 차단. 쿼리 없는 단순
  페이지 이동("지원하기" 링크 → 폼 페이지)은 통과(T2.3 이관). step(D17) → 문서 탐색은 method 와 관계없이 통과(그 버튼의 폼
  POST·GET 제출), 그 밖은 relaxed 와 같다. 판정·기록 중 예외가 나도 그 요청은 abort 한다(닫힌 쪽).
  막힌 기록은 출처만 싣는다(경로·쿼리에 입력값이 실릴 수 있다). 문서 탐색은 `aborted` 로 끊어 채우던 화면이 오류 페이지로
  바뀌지 않게 한다.
- **페이지 스크립트 층**: init script 가 모든 문서·프레임에서 submit 이벤트(캡처 맨 앞)·`form.submit()`·`requestSubmit()` 을
  **모드와 관계없이** 막는다 — 안전한 라벨의 `type=button` 이 핸들러로 폼을 제출해도 막힌다(→ `SUBMIT_BLOCKED`). 예외는 step
  창의 클릭 하나: 드라이버가 누르기 직전 그 제출 버튼을 토큰으로 등록하면(`guard_script.ALLOW_STEP`) submitter 가 그 버튼이고
  대상이 그 버튼의 폼인 submit 이벤트 **한 번만** 지나간다. 다른 폼·다른 버튼의 제출, `form.submit()`·`requestSubmit()` 은 step
  창에서도 막히고, 등록은 창을 닫는 수거(drain)가 지운다. 상태·기록은
  설치별 무작위 토큰으로만 바뀌고 수거된다. 이 층을 페이지가 비껴가도 네트워크 층이 문서 POST 를 막고, 반대도 같다(겹방어 —
  각자 혼자서 폼 제출을 막는지 테스트가 본다).
- **켜고 끄기**: 도구가 페이지를 처음 건드릴 때 켠다(`arm`, 못 켜면 `GUARD_UNAVAILABLE` 이고 동작하지 않는다 — 클릭은 가드가
  선 context 에서만). run 이 끝나면 `BrowserToolbox.close()` 가 끈다: init script 등록을 걷어 이후 문서(사람의 로그인·SSO
  자동 제출 폼)에는 심지 않고, route 는 앱 출처 차단만 남긴다. 부르지 않으면 켜진 채다(닫힌 쪽). 서비스 워커는 route 를
  비껴갈 수 있어 브라우저 기동 때 막는다(`service_workers="block"`). 모드 전환은 SubmitGuard 만 부른다 — 도구 인자(`click` 은
  ref 하나)·가이드·프롬프트로 닿는 통로가 없다. 사람 핸드오프(§A5 `request_login`·`request_human`) 동안에도 끄고, 재개 때
  다시 켠다(T2.5 이관). 꺼진 동안 대화상자는 처리하지 않고 사람에게 둔다(처리기를 떼면 Playwright 가 자동으로 닫으므로 처리기는
  두고 손대지 않는다). 켜져 있는 동안은 OS 파일 선택 창(`filechooser`)을 가로채 파일 없이 흘려보내고 대화상자처럼 알린다 —
  파일은 `upload` 로만 들어가고, 꺼지면 사람이 창을 쓸 수 있다. 켤 때는 프레임마다 가드가 **우리 것**인지 확인한다: 가짜
  토큰엔 null, 진짜 토큰엔 기록 배열을 돌려줘야 한다(`guard_script.VERIFY`). 가드가 꺼진 동안 열린 문서(로그인 뒤 돌아온
  지원 페이지)가 가드 이름을 먼저 차지했으면 켜지 않은 것으로 보고 `GUARD_UNAVAILABLE` 이다(닫힌 쪽).
- **check·입력 도구**: `check` 는 체크박스를 클릭하므로 click 과 같은 분류·창을 거친다(onchange 발신, T2.4 이관).
  fill·select·upload 도 relaxed 창 안에서 돌아 change 핸들러의 폼 제출·앱 출처 요청이 막힌다. 막혔어도 입력은 됐으므로
  FillLog 에 남기고 결과는 `SUBMIT_BLOCKED` 다.
- **L5**: 매 도구 호출의 앞뒤로 모든 프레임의 URL·보이는 글자 줄을 읽어 **직전 관찰과 비교**한다 — 새 줄·새 프레임 URL 에서만
  완료 근거를 찾는다(원래 있던 안내 문구로 오탐하지 않고, 창 사이에 뜬 완료 문구도 놓치지 않는다). 새 페이지로의 이동은 전부
  새 줄이라, 완료 문구가 있는 페이지를 `navigate` 로 열어도 INCIDENT 다. 근거가 나오면 그 run 은 어떤 도구도 받지 않는다.
  차단된 beacon 뒤에 페이지가 스스로 완료 문구를 띄우는 사이트도 INCIDENT 다 — 막았다는 것과 다른 통로로 새지 않았다는
  것을 하네스가 구분할 수 없어서다(닫힌 쪽).
- **다단계 사이트 — 단계 이동 자동 통과(D17, T2.7)**: 사람 승인은 **최종 제출 1회**다. `type=submit` 이라도 라벨 전체가 단계
  어휘(`submit_vocabulary.STEP_PHRASES` — 다음·다음 단계·계속·저장 후 계속·Next·Continue·Save and continue…, 정확 일치라
  "다음에 제출"·"Continue to review" 는 아니다)이고 dialog 밖이면 분류기가 **step** 을 준다. 단 같은 페이지에 **마지막 단계
  신호**가 있으면 risky(`LAST_STEP`) — `last_step_signal`: 진행 표시가 마지막(DOM `aria-current="step"` 이 목록 마지막 칸,
  글자 "단계 3/3"·"Step 3 of 3"·"총 3단계 중 3단계"), 최종 동의·제출 전 확인 문구(제출 전·최종 제출·제출 후에는·위 내용이
  사실…/before you submit·I certify…), 편집 가능한 입력칸이 없는 화면(검토·요약 페이지). `type=button` 단계 어휘 버튼도
  신호가 있으면 risky 다(fetch 로 최종 제출하는 마지막 "다음"). 신호 재료는 클릭 직전에 새로 읽은 `PageText`(입력칸 수 —
  체크박스 제외·숨은 파일 입력 포함, 폼 제출 버튼 수, 진행 표시)이고, 관찰이 없으면 step 은 없다. 제출 어휘·애매한 라벨은
  지금처럼 strict 다. step 창도 strict 꼬리 뒤에 열고, 꼬리를 기다렸으면 그 뒤 화면으로 다시 판정한다. `check` 는 step 창을
  열지 않는다(step 판정이면 strict — 사후 확인은 click 에만 있다). step 창은 그 버튼의 폼 제출만 통과시키고(위 스크립트 층·요청
  판정) 결과를 **사후 확인**한다(`SubmitGuard.after_step` → `step_landing`): 완료 근거 → INCIDENT(L5), 입력칸이나 폼 제출
  버튼이 있는 화면 → 계속(입력칸
  없는 검토 페이지도 제출 버튼이 남아 있으면 아직 제출 전 화면이다), 둘 다 없으면 2초(`STEP_LANDING_S`) 더 지켜본 뒤 run 을
  멈추고 `request_human` 과 같은 핸드오프로 사람에게 넘긴다(애매하면 닫힌 쪽). 화면이 바뀌었으면(주 URL 이 바뀌었거나 전
  화면 글자 줄의 절반 이상이 사라짐) 페이지 단계 `step` 이 +1 되고, 검증 오류 한 줄이 더해진 것은 같은 화면이다. FillLog
  항목과 ReviewRecord 의 `step` 은 이 페이지 단계다 — 승인 뒤 값 대조(L6 a)는 마지막 단계의 칸만 DOM 에서 다시 읽을 수 있다.
  단계마다 승인하던 설계(L6 확장)는 없앴다. dry_run 에서도 step 은 누른다 — dry_run 은 "사이트에 아무것도 안 보냄"이 아니라
  "최종 제출을 하지 않음"이다(D17). fetch 로 단계를 저장하는 사이트(`type=button` "다음")는 지금처럼 relaxed 로 통과한다.
- **입력 정규화(T2.4 이관)**: 브라우저가 한 줄 칸의 줄바꿈을 지우므로 `fill` 은 `normalize_fill_value`(한 줄 칸은 CR·LF 제거,
  url·email 은 앞뒤 ASCII 공백도 제거, textarea 는 CR LF→LF) 로 정리한 값을 넣고 **그 값을** FillLog 에 기록한다 — L6 대조가
  DOM 값과 같은 문자열을 본다.
- **남는 위험**(L5 가 뒤를 받친다): **마지막 단계 신호 없이 "다음" 라벨로 최종 제출하는 사이트**(D17 이 받아들인 위험 — step
  창이 통과시키고 완료 문구가 있으면 L5 가 곧바로 멈춘다, 짐 `next_final_nosignal`), step 창에서 그 버튼의 핸들러가 여는 다른
  문서 탐색(`location.href` — 문서 탐색은 통과시키므로 사후 확인만 본다), 완료 문구도 입력칸도 없이 폼 하나가 남는 완료 화면,
  안전한 라벨의 버튼이 fetch 로 최종 제출하는 사이트(relaxed 가 통과시킨다 — L2 허용 목록의 한계. 마지막 단계 신호가
  있으면 단계 어휘 버튼은 strict 다), 입력값을 싣지 않는 GET 하위 요청·`navigate` 로 여는 GET 제출 주소(입력값을 실으면
  `navigate` 가 거부), 3초보다 늦게 미룬 제출, 이미 열린 WebSocket 으로 보내는 제출, 리다이렉트로 앱 출처에 닿는 요청(Playwright route 는 첫 URL 만
  본다 — §A10 Host·Origin·토큰 검사가 막는다). shadow DOM 안 폼의 submit 이벤트(composed 가 아니라 window 캡처에 닿지
  않는다)는 페이지 스크립트 층이 못 봐 네트워크 층만 남는다(문서 POST·입력값을 싣는 탐색은 여전히 막힌다). 사람 핸드오프로
  가드가 꺼진 동안에는 그 전에 페이지가 3초 넘게 미뤄 둔 제출이 나갈 수 있다 — 끄기 전 strict 꼬리는 기다리고, 재개 때
  넘기기 직전 화면과 비교하는 L5 가 본다.
- **테스트 짐(gym)**: `tests/fixtures/sites/` 에 로컬 정적 사이트 — SPA fetch 제출, multipart 제출, confirm 대화상자 제출,
  "지원하기"가 폼 여는 버튼인 경우, 다단계 저장(fetch·`type=submit` 폼 POST 두 종류), iframe 폼, 제출 어휘가 없는 버튼(`확인`),
  링크로 여는 폼 페이지, GET 탐색 제출, confirm 수락 뒤 제출하는 "다음", 지연(setTimeout) 제출, 체크박스 onchange 제출,
  마지막 "다음"이 최종 제출인 사이트(진행 표시 신호 있음·신호 없음), 검토 페이지의 "계속"이 최종 제출인 사이트 등.
  **모든 픽스처에서 FILL 단계 제출 성공 0건**이 하네스 PR 의 통과 조건이다 — 보이는 칸을 다 채우고 누를 수 있는 것을 다 누르는
  적대 스크립트(`tests/gym/adversary.py`)로 짐 서버가 받은 요청을 센다. 예외는 매니페스트에 `accepted_risk`(결정 번호)가 붙은
  사이트뿐이고(지금은 D17 의 `next_final_nosignal` 하나), 거기서는 제출 1건과 INCIDENT 를 단언한다. 층을 하나씩 끄면(분류·
  네트워크·GET 규칙·strict 꼬리·대화상자·사후 감지·마지막 단계 신호) 해당 사이트에서 제출이 새는 것도 테스트한다
  (`test_submit_guard_layers_native.py`).

## §A5 BrowserToolbox (에이전트 도구)

한 곳에서 정의하고 두 런타임이 공유한다: API 경로는 in-process tool, CLI 경로는 같은 함수를 MCP(HTTP, 127.0.0.1, run 토큰)로 노출.

| 도구 | 설명 |
|---|---|
| `snapshot()` | 접근성 트리 + ref (agent-browser 스타일). 비밀번호 필드 값은 가림 |
| `navigate(url)` · `back()` · `scroll(ref?)` · `wait_for(text|ms)` | 이동 |
| `click(ref)` | §A4 L2/L3 경유. 인자는 ref 하나. 단계 이동 버튼은 하네스가 확인하고 통과시킨다(D17) |
| `fill(ref, value, source)` · `select(ref, option, source)` · `check(ref, on, source)` | `source` = 근거(profile 필드 / fact_id / answer_kb / generated / user). FillLog 에 자동 기록 |
| `upload(ref, document_id)` | 앱이 관리하는 파일만 (임의 경로 금지) |
| `generate_document(kind, …)` | 공고맞춤 이력서/포트폴리오 PDF, 자소서 문항 답변 — DocumentService 호출(§A7) |
| `ask_user(question, field_hint, options?, sensitive?)` | 실행 일시정지 → UI 질문. `sensitive=true` 면 답변 KB 에 저장 안 함, 에이전트도 값을 못 봄 |
| `request_login(site)` · `request_human(reason)` | 로그인 벽 · CAPTCHA·본인인증 → 사람 핸드오프(가드 꺼짐, 도구 거부) 후 재개 |
| `ready_for_review(submit_ref, notes)` | FILL 종료. 제출 대상 요소 기술자(선택자 후보·텍스트·위치)와 FillLog 확정 |
| `report_failure(reason)` | 진행 불가 |
| `commit_submit()` | **SUBMITTING run 에서만 노출.** 실제 클릭은 하네스가 (§A4 L6) |

- 구조(T2.4): 도구 목록은 `services/browser_toolbox_specs.TOOLS` 한 곳(이름·설명·입력 모델), 입력 모델은
  `contracts/browser_tools.py`(`extra=forbid`, 에러에 입력값 없음), 실행은 `BrowserToolbox.call(name, args)` 하나 —
  검증 실패·거부도 예외가 아니라 `ToolResult(ok=false, error)` 로 돌려준다. DOM 동작은 `PageDriver` port
  (`ports/browser.py`, 실제 `adapters/browser/playwright_pages.py` · 대역 `fake_pages.py`). 탭 조작(새 탭·팝업·닫기)은
  port 에 올리지 않는다 — run 은 BrowserHost 작업 탭 하나만 쓴다. `click` 은 하네스 장치를 더한
  `GuardedPageDriver`(§A4 SubmitGuard 설계)에만 있고, 페이지를 건드리는 도구는 전부 SubmitGuard 창 안에서 돈다.
  `ready_for_review` 는 제출 대상(요소·조상 기술자, 선택자 후보, 프레임·페이지 URL, 문서 좌표)과 L2 판정·메모·`step`·FillLog 를
  `ReviewRecord`(`contracts/submit_guard.py`, 고유식별정보 없어야 만들어짐)로 확정하고 run 을 끝낸다. 요소는 누르지 않는다.
  L2 판정은 그 순간 화면(`SubmitGuard.last_seen`)으로 해서 마지막 단계 신호도 기록된다. `click` 과 단계 이동 사후 확인은
  `browser_toolbox_click.py`(§A4 D17).
- ref 는 `e<N>` — snapshot 마다 새 번호(재사용 없음)라 옛 ref 가 다른 요소를 가리킬 수 없다. 드라이버가 요소 핸들을
  Python 쪽 표에 쥐고(DOM 에 표시 속성을 심지 않아 페이지가 ref 를 위조할 수 없다), 이동·새 snapshot 에 표를 버린다.
  snapshot 은 모든 프레임(iframe)을 훑고(`node.frame`), 숨긴 파일 입력도 싣는다. shadow DOM 은 아직 보지 않는다.
- 비밀 칸 = `type=password` 또는 autocomplete `current-password`·`new-password`·`one-time-code`(절대 규칙 3).
  snapshot 은 값을 읽지 않고 DTO(`SnapshotNode`)가 한 번 더 버린다. `fill` 은 toolbox(snapshot 기준)와 드라이버(동작 순간의
  DOM 재확인) 두 겹에서 거부한다. 규칙은 `domain/page_elements.py`.
- 동작 범위: `fill` = 텍스트 input·textarea·contenteditable(값 설정이지 키 입력이 아니라 Enter 암묵 제출이 없다),
  `select` = 네이티브 select(라벨, 없으면 value), `check` = 네이티브 checkbox·radio 만(role=checkbox 요소는 클릭 핸들러가
  있어 `click` 몫), `upload` = 파일 입력에 문서 **바이트**(`DocumentReader.read_document(user_id, id)` — 경로 인자 없음).
- `navigate` 는 http(s) 만(`javascript:`·`data:`·`file:` 는 L1 우회 통로) + `forbidden_origins` 거부 — 앱 자신의 콘솔을
  자동화 브라우저에서 열면 같은 출처가 되어 승인 API 를 부를 수 있다. 루프백 별칭(127/8·localhost·::1)은 포트로 묶는다.
- FillLog(`contracts/fill_log.py`): 성공한 fill/select/check/upload 만 `(seq, step, action, ref, field{role, name, 프레임 URL},
  source, value|checked|document_id)` 로 쌓는다(fill 값은 정규화한 값, §A4). `source={kind, key}` — profile·fact·answer_kb 는 key 필수,
  generated·user 는 선택, upload 는 문서가 근거라 없다. 값에 주민등록번호 꼴이 있으면 사이트엔 넣되 기록하지 않고
  `withheld=true`(재입력 때 다시 묻는다, 절대 규칙 5). snapshot·`report_failure` 이유도 에이전트·기록에 넘기기 전에 같은 꼴을 가린다.
- `ask_user`/`request_login` 은 도구 호출이 UI 응답(asyncio Future)을 **최대 `HUMAN_WAIT_S`초**(기본 600) 기다린다. 넘기면 run 을
  `NEEDS_INPUT` 으로(`request_login` 은 `NEEDS_LOGIN`) 종료하고, 답이 오면 **재진입 run** 이 FillLog 부분 기록부터 이어간다 (D8 과 같은 메커니즘).
- **ask_user (T3.4, D10)** — `services/browser_toolbox_ask.py`. 같은 HumanGate 통로(`HumanTaskKind.QUESTION`, 질문·field_hint·
  options·sensitive 를 싣고 답은 `HumanReply.answer`). 사람은 브라우저가 아니라 웹 UI 에서 답하므로 **가드를 끄지 않는다**
  (다른 도구 호출은 기다리는 동안 `AWAITING_HUMAN`). 끝난 모양: 답 → 결과 `answer{source, value}` 로 run 계속 · 거절·빈 답 →
  `answer_declined`(run 계속 — 그 칸 없이 가거나 report_failure) · 타임아웃 → `NEEDS_INPUT` 으로 run 끝.
  - 보통 답: 답변 KB 에 upsert(`ProfileService.remember_answer`, 같은 질문 키면 갱신, 출처 지원 건은 처음 것 유지) →
    `source={answer_kb, 답변 id}` 와 값을 에이전트에게. KB 저장이 실패해도 답은 `source={user}` 로 이번 run 에 쓴다.
  - **가린 답**(`sensitive=true` 또는 주민등록번호 꼴, 절대 규칙 5): KB·DB·job payload·run 기록·로그에 싣지 않고 **에이전트에게도
    값을 주지 않는다**(transcript 가 곧 기록이라). 값은 toolbox 메모리(핸들 = 질문 id)에만 있고, 에이전트는
    `fill(value="")`·`select(option="")` 에 받은 `source={user, key: 핸들}` 을 넣는다 — 앱이 값을 넣고 FillLog 는 `withheld`.
    snapshot 은 그 값이 든 칸의 값을 `(가린 답)` 으로 바꿔 보낸다(짧은 답이면 다른 칸도 가릴 수 있다 — 닫힌 쪽). 가린 답은 L3
    GET 대조(입력값이 URL 에 실리는가)에도 들어간다. 핸들이 없는데 빈 값으로 fill 하면 거부(빈 칸으로 덮지 않는다).
  - **재진입 run**(`runner/fill_reentry.py`): 타임아웃이면 run 이 질문(HumanTask, 답 없음)을 `runs/<run_id>/human_task.json` 에
    남긴다. 사람이 나중에 `FillReentry.answer(run_id, 답)`(Container.reentry, UI 는 M4) — 지원 건이 NEEDS_INPUT 이고 이미
    대기·실행 중인 fill job 이 없을 때만 — 답을 위 규칙대로 갈무리(가린 답은 `HeldAnswers`, 프로세스 메모리)하고 fill job 을
    넣는다(payload `{resume_from: run_id, answer: source}` — 값 없음). 새 run 은 같은 URL 을 처음부터 열고, 프롬프트
    `[이어서 — 직전 run]` 절에 직전 FillLog(단계·칸·값·source, withheld 는 "값 기록 안 됨")와 받은 답을 받아 값을 다시 넣는다(새
    FillLog 에 새로 쌓인다). 가린 답은 NEEDS_* 로 끝나면 다음 재진입 몫으로 남기고, 다른 끝(승인 대기·실패·INCIDENT)이면 버린다.
    앱이 다시 시작돼 가린 답이 사라졌으면 프롬프트가 다시 묻게 한다.
  - fill 프롬프트는 `[답변 KB]` 절에 사용자의 답변 전체(`id: 질문 키 → 답`)를 싣는다 — 에이전트는 프로필·KB 에 없을 때만 묻는다.
- **사람 핸드오프(T2.6)**: 기다리는 통로는 `ports/human_gate.HumanGate`(`wait(task, timeout_s)`·`pending()`·`answer(id, reply)`)
  — 구현은 `adapters/human_gate/memory.py`(asyncio Future, 앱이 단일 프로세스라 이것이 실제) · `scripted.py`(사람 대신 미리 적은
  대로 답하는 대역). UI 연결(M3/M4)은 `pending()` 을 보여 주고 `answer()` 한다. 일(`HumanTask`)에는 에이전트의 말(site·reason)과
  별개로 하네스가 직접 본 근거(`signal`)·탭 주소가 실리고, 고유식별정보는 가린 뒤 만든다. 흐름: L5 관찰 → 하네스 snapshot →
  **대기 표시를 먼저 세우고** 가드를 끈다(strict 꼬리는 기다림) → 사람을 기다린다 → `DONE` 이면 가드를 다시 켜는 데 성공해야
  도구가 돌고(`_page()` 가 켜기부터 — 실패하면 `GUARD_UNAVAILABLE`, 이후 도구도 같다), 넘기기 직전 화면과 비교한 L5 근거가
  있으면 `INCIDENT` 다(사람이 스스로 제출한 경우 포함). `TIMED_OUT`·`DECLINED` 면 `NEEDS_LOGIN`/`NEEDS_INPUT` 결과로 run 이
  끝나고(`BrowserToolbox.needs_human`), 사람이 계속 쓰도록 가드는 꺼 둔다(`close()` 와 같다). 도구 호출은 **직렬**(asyncio
  Lock) — 핸드오프가 다른 동작의 창 도중에 가드를 끄지 못하고, 기다리는 동안 온 호출은 잠금을 기다리지 않고 바로
  `AWAITING_HUMAN` 이다. 대기 상한은 `BrowserToolbox(human_wait_s=…)` — 설정 `HUMAN_WAIT_S`(기본 600초, T3.4).
- 사람 몫 감지(`domain/human_handoff.py`, 순수): 보이는 비밀번호 칸(autocomplete 포함)·로그인 주소(경로 조각이 정확히
  login·signin·auth·sso·oauth… 이거나 호스트 첫 이름이 accounts·login·nid…) → 로그인 벽, one-time-code 칸 → 본인인증,
  CAPTCHA 제공자 프레임(reCAPTCHA·hCaptcha·Turnstile…) 안의 누를 수 있는 요소·"로봇이 아닙니다"·"자동입력 방지"·보안문자 칸 →
  CAPTCHA. `snapshot` 결과의 `handoff` 에 근거가 붙고 어느 도구로 넘길지 안내한다. CAPTCHA 제공자 프레임 안의 요소(보이지
  않는 v3 배지 포함)와 CAPTCHA 답 칸은 `click`·입력 도구가 `CAPTCHA` 로 거부한다(절대 규칙 3). 비밀번호 칸은 원래대로 `fill`
  이 거부한다.
- 탭 조작은 핸드오프에도 port 에 올리지 않는다(T2.1 이관 결정): 사람이 SSO 팝업·새 창을 다루고, 재개는 `BrowserHost.page()`
  (작업 탭이 살아 있으면 그 탭, 닫혔으면 마지막 열린 탭)로 이어간다. 사람이 브라우저를 닫았으면 다시 띄우고 가드를 새로 건다.
- Google 로그인 실측(T2.1 이관, 2026-09-30, 전용 프로필·설치 Chrome, 계정 입력 없이 진입 화면까지): `accounts.google.com`
  식별자 화면이 정상으로 뜨고 차단 문구("안전하지 않은 브라우저" 류)는 없다. 다만 `navigator.webdriver` 는 `true` 다 —
  Google 의 차단 판정은 식별자를 낸 뒤에 나오므로 계정 없이 끝까지는 확인하지 못했다. 그래서 기동 플래그는 바꾸지 않는다:
  webdriver 신호를 숨기는 것은 봇 탐지 우회라 절대 규칙 3 밖이다. 사람의 첫 실사용에서 막히면(M3/M4 UI 에서 보이게) 사람이
  Google 계정 대신 사이트 자체 로그인을 쓰도록 안내한다.

## §A6 AgentRuntime (port) — 구현 3개

| 구현 | 용도 |
|---|---|
| `ClaudeCliAgentRuntime` (기본, D5) | `claude -p --output-format stream-json --mcp-config <run별 설정> --strict-mcp-config`, 내장 도구 전부 비활성, 우리 MCP 도구만 allow. v2 `ClaudeCodeCliLLM` 의 프로세스 관리·장애 시그니처(로그인 풀림/한도초과) 재사용 |
| `AnthropicApiAgentRuntime` | Messages API tool-use 루프, 같은 BrowserToolbox 를 in-process 로 |
| `ScriptedAgentRuntime` | 테스트 대역 — 미리 정한 도구 호출 시퀀스 재생 (gym 테스트·상태기계 테스트용) |

- 시스템 프롬프트 = 역할/규칙 + 전역 가이드 + 도메인 가이드(§A8) + run 종류별 지시 + (재진입이면) FillLog/피드백.
- port(T3.3, `ports/agent.py`): `run(system_prompt, tools: [AgentTool], call_tool, *, limits) -> AgentOutcome`
  (DTO `contracts/agent.py`). 런타임은 브라우저를 직접 만지지 않는다 — 도구는 호출자가 넘긴 `call_tool(name, args) ->
  ToolReply{ok, content(JSON), done}` 로만 부르고, 없는 이름·잘못된 인자도 그대로 넘겨 에러 답을 받고 이어간다. `done` 을
  받으면 멈춘다. 도구 목록은 `browser_toolbox_specs.agent_tools()`(TOOLS 의 입력 모델 JSON Schema). 계약 테스트
  `tests/ports/test_agent_runtime_contract.py`(CLI·API 구현이 params 로 붙는다).
- fill run(`runner/fill.py` `FillRunHandler`, JobRunner 의 `fill` 핸들러): run 기록 시작 → QUEUED→FILLING → 프롬프트
  (`ai/fill_prompt.py` 순수 빌더 — 프로필 요약 줄의 키가 FillLog source key) → 런타임 → **도구 결과로만** 전이
  (`runner/fill_session.decide`, 위에서부터 첫 일치). 에이전트 출력 텍스트는 상태에 영향이 없다.

  | 끝난 모양 | 지원 건 | run |
  |---|---|---|
  | L5 제출 흔적(`incident`) | INCIDENT | failed |
  | `ready_for_review` | AWAITING_APPROVAL (ReviewRecord 저장) | done |
  | 사람 대기가 끝나지 않음(`needs_human`) | NEEDS_LOGIN · NEEDS_INPUT | done |
  | 도구가 `guard_unavailable` — 그 호출에서 run 을 끝낸다(닫힌 쪽) | FAILED (재시도 없음) | failed |
  | `report_failure` · 도구 수/시간 한도 · 끝 도구 없이 멈춤 | FAILED | failed |

  끝난 모양은 핸들러가 직접 전이하고 job 은 DONE 이다. 핸들러 밖으로 나가는 것은 예외(브라우저 기동·런타임 장애 등)뿐 —
  run 을 failed 로 닫고 다시 던져 §A9 재시도 정책에 맡긴다. 끝 전이가 그 사이 취소 등에 밀리면(`InvalidTransition`)
  run 을 failed 로 닫고 러너가 CONFLICT 로 둔다. 정지(취소)면 run 을 닫지 않고 가드도 내리지 않는다 — 크래시 복구가
  INTERRUPTED·QUEUED 로 되돌린다.
- 한도(`AgentLimits`, 기본 도구 200회·1800초): 런타임이 지키고, `FillSession` 이 한 번 더 센다 — 넘친 호출은 도구에 닿지
  않고 `run_limit`(done) 로 돌아간다. 런타임이 멈춰 버리면 호출자가 한도+30초에 끊는다.
- 기록(`services/run_artifacts.py`): BlobStore `runs/<run_id>/review.json`(ReviewRecord) · `fill_log.json`(끝난 모양과 상관없이
  FillLog 부분 기록 — 재진입 run 이 읽는다). 저장 직전에 고유식별정보를 한 번 더 본다.
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
- **AnswerKB**: (정규화 질문 키, 답, 출처 지원 건, 갱신일). fill 프롬프트에 전부 실리고(§A5), 없으면 `ask_user` 로 묻고 답을 upsert
  (`sensitive`·주민번호 꼴 답은 저장 안 함).
  정규화(`domain/question_key.py`): NFKC → zero-width 제거 → casefold → 공백 하나로 접기 → 앞뒤 문장부호·기호
  (`*`·`?`·`:`) 제거. 공백은 지우지 않는다 — 질문 원문 필드가 없어 키가 곧 화면 표시다. 같은 키 생성은 409, 덮어쓰기는 PUT 으로.
- **Document**: 사용자 업로드 고정 파일 / 생성 파일(PDF) — 버전·생성 근거(fact_ids) 보관.
  업로드 규칙(`domain/uploads.py`): `pdf·docx·png·jpg(jpeg)` 만, 확장자와 바이트 시그니처가 둘 다 맞아야 하고 content type 은
  서버가 확장자로 정한다(클라이언트 값 무시). 상한 `DOCUMENT_MAX_BYTES`(기본 10 MiB). 파일명은 제어·서식 문자(Cc·Cf) 제거 →
  `/`·`\` 앞부분 제거(basename) → 255자 상한(확장자 유지)으로 표시용만 남기고, blob 키는 `documents/{user}/{doc_id}{ext}`.
  multipart 는 python-multipart(Starlette `MultiPartParser`) 스트리밍 — 본문은 상한 + 64 KiB 까지만 흘리고, 파일 1개·필드 4개
  상한, 닫는 경계까지 오지 않은(잘린) 본문과 파트 헤더 과대는 422. 파일 파트는 1 MiB 넘으면 임시 파일로 흘러 peak ≈ 파일 1벌.
  `UploadService`(`services/uploads.py`)가 저장·삭제를 맡는다: 메타 저장이 실패하면 방금 쓴 바이트를 지우고, 삭제는 메타·경험
  첨부 참조를 한 트랜잭션으로 지운 **뒤** `BlobStore.delete` 로 바이트를 지운다(고아 파일 0).
  **본문 주민등록번호 검사**: PDF·DOCX 는 저장 전에 `DocumentTextExtractor` 로 글자를 뽑아 주민등록번호 꼴이 있으면
  `unique_identifier_rejected`(422, 바이트·메타 둘 다 남기지 않고 에러에 원문 없음). 온보딩 추출처럼 가리고 받지 않는
  이유: 고정 파일은 사용자가 사이트에 **그대로 제출하는 원본**이라 서버가 내용을 임의로 바꾸면 안 된다.
  한계: 글자를 못 뽑는 파일 — 이미지(png·jpg), 스캔본·암호 PDF, 추출기 상한(30쪽 등) 초과 — 은 검사하지 못한 채 받는다.
  PDF 검사는 파서가 바이트를 한 벌 더 읽어 업로드 메모리 peak 가 파일 크기의 3배 안팎이 된다.
- **ProfileService**(`services/profile.py`)가 저장소가 강제하지 않는 규칙을 맡는다: 경험·fact·답변 id 발급(fact `id` 를
  비워 보내면 발급), fact id 의 사용자 단위 유일성, 경험 `document_ids` 참조 검사, 소유자 검사(남의 것은 404). 로컬 1인
  설치라 사용자 id 는 `DEFAULT_USER_ID` 상수 하나다.
- **온보딩 추출** (`services/profile_drafts.py`·`resume_extraction.py`): 이력서 파일(PDF/DOCX) → `DocumentTextExtractor` port
  (실제: `adapters/extract/pdf_docx.py` — pypdf, DOCX 는 zip+WordprocessingML 직접 파싱 / 대역: 등록한 바이트만 읽는
  `FakeTextExtractor`, 테스트 전용이라 설정 선택지 없음) → LLM 구조화 추출(`ai/profile_extraction.py` 의
  `ProfileExtraction`, 스키마 위반은 2회 재프롬프트) → **초안**. 초안은 사용자가 확정하기 전까지 본 프로필과 떨어져 있다.
  - 추출 텍스트의 주민등록번호 꼴은 **LLM 에 보내기 전 메모리에서 가린다**(`redact_resident_registration_numbers`,
    탐지 규칙은 거부와 같은 `domain/unique_identifiers.py` 한 곳, 절대 규칙 5). 원문·가린 값·위치는 저장·로그 어디에도
    없고, 초안의 `redacted_identifiers`(개수)만 남아 웹이 "N개를 가렸습니다"를 안내한다. 고정 파일 업로드(위)와 달리
    가리는 이유: 추출 원문은 제출되지 않고 초안의 재료일 뿐이며, 번호가 든 이력서로도 온보딩할 수 있어야 한다.
    파일명에 번호가 있으면 LLM 호출 전에 422. 텍스트 5만 자 상한.
    추출기 상한: PDF 30쪽, DOCX 본문 XML 20 MiB(선언·실제 둘 다), DOCTYPE/ENTITY 가 있는 DOCX 는 거부.
  - 초안 저장(`services/draft_store.py`)은 BlobStore JSON `drafts/{user_id}/{draft_id}.json` — 검토 뒤 지우는 일회성
    작업물이라 테이블을 두지 않는다. 목록은 `BlobStore.list_keys(prefix)` 로 본다(읽을 수 없는 파일은 건너뜀).
    초안 id 는 `[A-Za-z0-9_-]{1,64}` 만. `ProfileExtraction`·초안 모델은 `IdentifierFree`.
  - API: `POST /api/profile/drafts/upload`(multipart `file` — 온보딩 기본 경로, 파일을 문서로 저장하지 않는다),
    `POST /api/profile/drafts {document_id}`(이미 올린 문서에서), `POST /api/profile/drafts/v2-import {profile_yaml, facts_yaml}`,
    `GET /api/profile/drafts`(목록: id·출처·출처 파일명·생성 시각·값 있는 인적사항 필드 수·경험 수·가린 개수, 최근 순 —
    새로고침 뒤 이어서 검토), `GET·PUT·DELETE /api/profile/drafts/{id}`(PUT = 검토 중 편집, 같은 스키마로 재검증),
    `POST /api/profile/drafts/{id}/confirm {profile_fields, experience_indexes}` — 고른 항목만 한 트랜잭션으로 병합하고
    초안을 지운다. 병합 규칙(`services/draft_merge.py`): 초안이 빈 칸·`None` 이면 기존 값 유지, 목록(링크·학력·스킬·어학)은
    없는 항목만 뒤에 붙인다. 경험은 `build_experience` 로 id·fact id·섹션 key(`s1`…)를 새로 발급받는다.
  - v2 임포터(`services/v2_import.py`, LLM 없음): v2 `profile.yaml`·`facts.yaml` 을 같은 초안 경로로. fact 를 `entity`
    (없으면 v2 `kind`)로 묶어 Experience, `block` 별로 section — `domain/experience_facts.py` 의 역방향이라 확정 뒤
    이력서 블록 구조가 v2 와 같다. YAML 별칭 거부, `user_id` 가 둘 이상이면 거부. 입력 양식 예시는
    `tests/fixtures/v2_config/`(리포 `config/` 는 없다).
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

- `jobs(id, kind, application_id, status, attempt, payload, created_at, run_after, started_at, finished_at, error)`
  (`0004`) — SQLite 행 잠금 대신 단일 프로세스 asyncio 러너가 소비. `status = queued|running|done|failed|interrupted`,
  `run_after` 는 백오프 재시도 시각(행에 남겨 정지·재기동에도 유지). `application_id` 는 지원 건 없는 작업(반성)을 위해 nullable.
  꺼내기(`claim_next`)는 조건부 UPDATE 라 두 소비자가 같은 행을 꺼내지 않는다. port 는 `ports/jobs.py`(`JobRepository`·
  `RunRepository`, UoW 의 `jobs`·`runs`), DTO(`JobRecord`·`RunRecord`·`JobKind`)는 `contracts/jobs.py`.
- 슬롯: 브라우저 필요 작업(fill/revise/submit)은 **동시성 1**(전용 크롬 창 하나), 생성·반성은 별도 슬롯(기본 2).
  핸들러 등록 표(`JobKind → handler`, bootstrap 이 넘긴다) — 없는 kind 는 job FAILED(지원 건도 QUEUED→FAILED).
- SQLite 트랜잭션은 `BEGIN IMMEDIATE` — 읽고 쓰는 트랜잭션(claim·transition) 둘이 DEFERRED 로 겹치면 쓰기 잠금 승격에서
  기다리지 않고 "database is locked" 로 실패한다. 처음부터 쓰기 잠금을 잡아 뒤에 온 쪽이 timeout(30초)까지 기다린다.
- 재시도(`domain/failure.py` `classify_failure`·`RetryPolicy`, 예외 정의는 `domain/errors.py`): 예외를 `FailureKind` 로 가른다.

  | 실패 | 예외 | 처리 |
  |---|---|---|
  | TRANSIENT | `BrowserLaunchFailed` · `LLMExecutionError`(아래 둘 제외) | 지수 백오프(기본 5초×2ⁿ, 상한 120초) 3회까지, 같은 행 `attempt+1`. 지원 건 FILLING→QUEUED |
  | NEEDS_LOGIN · NEEDS_INPUT | `AuthRequired` · `CaptchaEncountered` | 재시도 없이 그 상태로 |
  | INCIDENT | `SubmitIncident` | 재시도 없이 INCIDENT |
  | CONFLICT | `InvalidTransition` | 지원 건을 건드리지 않는다(취소 등이 먼저) |
  | FATAL | 그 밖 전부 — 하네스 불가(`SubmitGuardUnavailable`)·`LLMAuthRequired`·`LLMQuotaExceeded`·**모르는 예외** | 재시도 없이 FAILED(사람이 다시 시작) |

  모르는 예외는 인프라성인지 모르니 되풀이하지 않는다(닫힌 쪽). **submit 은 어떤 실패도 재시도·재투입하지 않는다**(이중 제출).
  재실행은 지원 건이 QUEUED 일 때(또는 지원 건 없는 작업)만 — 그 사이 취소됐으면 다시 줄 세우지 않는다.
- 기동: 소비 전에 크래시 복구(§A3) — `RUNNING` run·job → `INTERRUPTED`, 지원 건은 `CRASH_RECOVERY`, 되돌린 지원 건이 QUEUED 면
  같은 작업을 새 job 행으로(크래시는 시도 1회로 센다 — 같은 작업이 매번 죽이는 루프를 한도에서 끊고 FAILED).
- 정지: 진행 중 job 을 취소하고 같은 복구를 돈다(정지는 시도로 세지 않는다). 대기 중(QUEUED) job 은 그대로 남아 다음 기동에 소비된다.
- 에러 문자열은 주민번호를 가린 뒤 500자로 자르고, 저장소는 job payload·에러·run 에러의 고유식별정보를 거부한다(절대 규칙 5).
- UI 갱신: 폴링(TanStack Query) 기본, 필요 시 SSE.

## §A10 보안 · 설정

- 서버는 `127.0.0.1` 만. MCP 엔드포인트는 run 별 일회성 토큰. HTTP 요청은 `api/security.py` 미들웨어가 세 겹으로 본다:
  1. `Host` 가 `127.0.0.1`·`localhost` 가 아니면 403 — DNS 리바인딩이면 공격 페이지가 "같은 출처"가 돼 CORS·Origin 이 못 막는다.
  2. `Origin` 이 **있으면** `WEB_CORS_ORIGIN` 이거나 이 서버와 같은 출처여야 한다(아니면 403, GET 포함). 브라우저는 변경 요청에
     Origin 을 항상 붙이므로 없는 요청은 브라우저 밖 로컬 프로세스다 — 그쪽은 3 이 막는다.
  3. 변경 요청(POST·PUT·PATCH·DELETE)은 `X-Auto-Apply-Token` 헤더가 설치별 토큰과 같아야 한다(아니면 403).
     토큰은 최초 기동에 `DATA_DIR/session_token`(0600, 원자적 생성)으로 만들고 재기동해도 유지한다.
     웹 콘솔은 `GET /api/session`(no-store) 으로 받는다 — 1·2 와 CORS 가 타 출처를 막는다.
     **한계**: 같은 PC 의 로컬 프로세스·다른 OS 계정도 `/api/session` 으로 토큰을 얻을 수 있다(파일 0600 으로는 못 막는다).
     D1 위협 모델(로컬 1인)상 허용하고, 막는 대상은 브라우저가 대신 보내는 타 사이트 요청이다 — 강화는 M7.
  보안 미들웨어는 CORS 안쪽이라 403 에도 CORS 헤더가 붙는다. CORS 허용 헤더는 `Content-Type`·토큰 헤더만.
- API 에러는 한 모양: `{"error": {"code", "message", "details": [{"loc", "msg"}]}}`. 검증 실패 상세에 **입력값을 싣지 않는다**
  (FastAPI 기본 422 의 `input` 은 거부한 주민번호를 되돌려준다, 절대 규칙 5). 코드: `validation_error`·`unique_identifier_rejected`
  (422) · `extraction_failed`(422, 문서에서 글자를 못 뽑음) · `not_found`(404) · `conflict`(409) · `too_large`(413) ·
  `unsupported_type`(415) · `host_not_allowed`·`origin_not_allowed`·`invalid_token`(403) ·
  `llm_invalid_output`·`llm_auth_required`·`llm_quota_exceeded`·`llm_error`(502, LLM 에러 문자열은 모델 출력 원문을 담을 수 있어
  응답에 싣지 않는다) · `internal_error`(500, 원인은 로그에만).
- 설정(`settings` 테이블 + 최초 `.env` 없이도 동작): `llm_backend=cli|api`, API 키(파일 권한 0600), `submit_mode`, 대기 타임아웃, 언어.
  `settings` 테이블 전까지의 우선순위: 환경변수 > (개발 모드일 때만) `./.env` > 사용자 설정 디렉터리 `.env`
  (`platformdirs.user_config_dir("auto-apply")`, `AUTO_APPLY_CONFIG_DIR` 로 이동 가능). 개발 모드 = `AUTO_APPLY_DEV=1`
  이거나 cwd 에 `name = "auto-apply"` 인 `pyproject.toml` 이 있을 때. 설치본을 아무 폴더에서 실행해도 그 폴더의 `.env` 가
  `DRY_RUN_ONLY`·`DATA_DIR`·API 키를 바꾸지 못하게 하려는 것이다(절대 규칙 2).
- CORS 는 `WEB_CORS_ORIGIN` 하나만 연다. `http(s)://127.0.0.1` / `http(s)://localhost` 출처만 허용하고 `*` 등은 설정 로드에서 거부한다.
  기본값은 **개발 모드에서만** `http://localhost:5173`(Vite). 설치본은 비어 있어 같은 출처만 허용한다 — 5173 포트에 아무 앱이나
  뜨면 그 앱이 토큰을 받아 가지 않게. API 문서(`/docs`·`/redoc`·`/openapi.json`)도 개발 모드에서만 연다.
- 기동 마이그레이션은 한 트랜잭션(pysqlite 트랜잭션 레시피 + `transactional_ddl`)이라 실패해도 스키마가 반쯤 남지 않는다.
  실패하면 `auto-apply` 는 DB 경로·원인 한 줄을 로그로 남기고 종료 코드 1 로 끝난다.
- 로그: structlog, **모든 로그에 `application_id`·`run_id` 구조화 필드.**
