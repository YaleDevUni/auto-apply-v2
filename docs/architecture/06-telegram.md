# Telegram Control Plane

> `docs/ARCHITECTURE.md` 색인의 §6. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 6. Telegram Control Plane

사람과의 인터페이스는 **버튼(callback)** 과 **자유 텍스트** 두 갈래다. 슬래시 명령은 두지
않았다 — 문법을 외워야 하는 표면을 하나 더 만드는 대신, 정해진 결정은 버튼으로 못박고
나머지는 채팅 에이전트가 도구를 골라 처리한다(아래).

```
알림 (Notifier → 사람)                       콜백 prefix
──────────────────────────────────────────   ─────────────
이력서 승인 요청 [승인][거절][✏️ 수정요청]      a / r / v
  └ 수정요청 → scope 선택 [이번만][항상][취소]  vc
  └ scope 선택 → ForceReply 자유 텍스트         (태그 파싱)
가이드 patch 2차 승인 [반영][무시][취소]        ga / gr / gc / gv
recipe 파손 확정 [🔧 진짜 깨짐][👌 아님]        qa / qr   → repair-{...} 워크플로우 (§2.4a)
recipe 승격 승인 [승격][보류]                   pa / pr   → repair-{...} 워크플로우
SUPERVISED 체크포인트 [✅ 계속][❌ 중단]         ca / cr   → CheckpointStore (signal 아님)
워크플로우/프로세스 이상 알림                    (버튼 없음, §11.2d)
```

- 콜백 데이터에 `nonce`를 넣고 `approvals.nonce`로 1회성 검증 → 버튼 재탭/오래된 메시지 재사용 방지.
- 명령 → FastAPI → `client.get_workflow_handle(f"application-{id}").signal(...)`.
  Telegram 핸들러가 DB를 직접 쓰지 않는다.
- 발신자 `chat_id` allowlist. 이 봇은 실제 제출 권한을 가진 콘솔이다.
- **REVISE(M3 연장)**: 승인/거절 버튼 옆에 "✏️ 수정요청"이 있다. 누르면 scope 선택(이번만/항상)
  버튼 → ForceReply로 자유 텍스트 피드백을 받는 3단계다. 이 자유 텍스트를 어느
  application/nonce/scope에 연결할지가 nonce와 같은 문제였다(발급 프로세스와 webhook/리스너
  프로세스가 갈라진다) — `[revise:{application_id}:{nonce}:{scope}]` 태그를 ForceReply 프롬프트
  본문에 실어 보내고, 사용자 답장의 `reply_to_message.text`에서 그 태그를 파싱해 복원한다
  (상태를 안 들고도 왕복). `telegram/bridge.py`의 `handle_message`가 이 답장을 처리한다.
- **dry-run 배지(M3 연장)**: 승인 요청 메시지 맨 앞에 🧪 DRY RUN / ⚠️ SUPERVISED / 🚨 LIVE /
  ❓ 확인 불가 배지를 붙인다 — dry-run인 줄 알고 안심했는데 recipe가 이미 candidate/active로
  승격돼 실제로는 제출되는 경우를 혼동할 위험 때문(메모리 dry-run-indicator-backlog).
  `_execution.resolve_mode()`가 실행 activity 안에서만 모드를 결정하던 걸,
  `ApplicationWorkflow._peek_mode`가 승인 요청 직전에 미리 알아내 `DecisionRequest.mode`에
  실어 보낸다 — `dry_run_only`면 recipe 상태와 무관하게 항상 DRY_RUN이라 조회 없이 바로
  정해지고, 아니면 recipe.status를 봐야 해서 `load_active_recipe`를 한 번 더 부른다(가벼운
  read라 `_execute`가 실행 시점에 다시 부르는 것과 중복 호출을 감수). 조회가 실패하면 mode를
  None으로 둬 "확인 불가" 배지로 보여준다 — 잘못된 낙관적 배지보다 모른다고 말하는 쪽이 안전.
  이 배지는 승인 요청 시점의 스냅샷이라 그 뒤 recipe 상태가 바뀌면(드묾) 실제 실행 때와
  달라질 수 있다는 한계는 남는다. 가이드 patch 2차 승인(`guide_patch=True`)은 실행과 무관해
  배지를 안 붙인다.
- **주의사항 인디케이터(M3 연장)**: dry-run 배지와 같은 동기 — 승인 버튼을 누르기 전 정보
  비대칭을 줄인다(메모리 wanted-application-caution-indicators-backlog). `DecisionRequest`에
  세 필드를 추가로 실어 `_await_decision`이 채운다. (1) `caution_documents` — 성적증명서·
  경력증명서처럼 자동화가 절대 못 채우는 첨부서류 힌트(`domain/job_applicability.
  caution_documents`, `job.title`+`job.description`을 정규식으로 스캔하는 순수 함수라
  workflow 안에서 I/O 없이 바로 부른다). 포트폴리오는 이미 자동 첨부되므로 제외한다 — 지원
  가능 여부를 막는 `evaluate_applicability`의 `requires["documents"]`/`DOC_MISSING`
  blocker 판정과는 기준이 다르다(그쪽은 보유 여부와 대조해 막을지 말지를 정한다, 이건 보유
  여부와 무관하게 "폼엔 어차피 못 채운다"는 사실 자체를 알린다). (2) `portfolio_filename` —
  이번 지원에 실제로 첨부될 포트폴리오 파일명. `adapters/resume/_assemble.py`가 이미 계산해
  `ResumeDraft.content`에 실어둔 값을 그대로 옮긴다(새 계산 없음). (3) `caution_notes` — LLM이
  이력서 생성 콜에서 같이 내는 주관적 주의사항 자유 서술(`ai/schemas.ResumeContentSchema.
  caution_notes`, [[portfolio-category-llm-step]]과 같은 패턴 — 같은 콜에 필드 하나 얹는 쪽이
  별도 LLM 콜보다 싸다). 근거 fact_id가 필요한 서술이 아니라 공고에 대한 메타 코멘트라
  `ground_check`가 검증하지 않는다. 셋 다 본 승인 요청에만 채워지고, 가이드 patch/repair
  승격/체크포인트 같은 중첩 승인은 실행과 무관해 안 붙는다(`_mode_badge`와 같은 조건).
- **recipe 파손 확정(§2.4a)**: 수선 에이전트를 돌리기 전에 "이 recipe 진짜 깨진 건가요?"를
  먼저 묻는다 — 실행 실패의 상당수가 recipe 와 무관하기 때문이다(이미 지원한 공고/로그인
  만료/마감). 메시지에는 실패 시점 DOM 판정(`domain/recipe_diagnosis`)을 근거로 붙인다.
  `qa`(확정)를 누르면 그 즉시 recipe 가 격리돼 **그 플랫폼 제출이 멈추고** 수선이 시작되므로,
  버튼 라벨과 본문에 그 부작용을 그대로 적는다. `qr`/무응답(24h)이면 아무것도 안 바뀌어 다른
  지원 건은 계속 제출된다. 격리 해제는 승격 성공 또는 채팅 도구 `unquarantine_recipe`.
  `pa`/`pr`과 같은 워크플로우를 겨누지만 슬롯·nonce 는 분리돼 있다 — "수선해도 된다" 클릭이
  "새 recipe 를 active 로 올려도 된다"로 소비되면 안 된다.
- **체크포인트 승인(§2.4c)**: SUPERVISED 실행 중 페이지 경계마다 스크린샷 + "✅ 계속/❌ 중단"
  2버튼(`ca`/`cr`)을 보낸다. 다른 콜백과 달리 워크플로우 signal이 아니라
  `checkpoint_store.record_decision`을 직접 호출한다 — 기다리는 게 워크플로우가 아니라
  activity 자신이기 때문이다.
- **자유 텍스트 채팅 에이전트**: REVISE/가이드 patch ForceReply 태그에 안 걸리는 자유 텍스트는
  더 이상 무시되지 않고 `telegram/agent.py`의 ReAct 루프로 간다 — 슬래시 커맨드 없이 채팅으로
  물으면 LLM이 매 턴 `LLMClient.structured()`로 `AgentStep`(도구를 부를지 최종 답을 할지) 하나만
  고르고, 실제 도구 실행은 `telegram/_agent_tools.py`의 `TOOLS` 레지스트리가 한다
  (`agent.py`는 루프 오케스트레이션만, 도구 구현은 별 파일로 — "한 파일 = 한 책임").
  현재 도구 12개:

  | 도구 | 성격 | 파일 |
  |---|---|---|
  | `list_applications` / `get_application` | 읽기. job 캐시에서 회사/직무를 찾아 붙인다 | `_agent_tools.py` |
  | `list_recipe_versions` | 읽기 | `_agent_tools.py` |
  | `start_applications(count, dry_run)` | 적합도 상위 N건 워크플로우 시작 | `_agent_tools.py` |
  | `apply_by_url(url)` | 사람이 지정한 wanted 링크 1건 시작 | `_agent_tools.py` |
  | `retry_application(application_id)` | 실패 건 재시도 — URL 없이 job 캐시 역조회 | `_agent_tools_retry.py` |
  | `collect_now()` | 공고 수집을 스케줄 기다리지 않고 즉시 1회 | `_agent_tools_collect.py` |
  | `resend_pending_decision(id)` / `resend_all_pending_decisions()` | 승인 버튼 재전송 (§11.2g) | `_agent_tools_resend.py` |
  | `schedule_status()` / `set_schedule_enabled()` / `set_schedule_time()` | Schedule 조회·on/off·시각·건수 (§11.2f) | `_agent_tools_schedule.py` | `resend_pending_decision`은 워크플로우를 직접
  mutate하지 않는다 — `ApplicationWorkflow.pending_decision` query로 nonce를 읽어와
  `TelegramNotifier.resend_decision`으로 **원래 승인/거절/수정요청 버튼과 같은 콜백을 다시
  보낼 뿐**이다. 실제 승인/거절/제출은 여전히 사람이 그 버튼을 누르는 순간에만 일어난다 —
  절대규칙 4를 자연어 오인식 경로로 우회하지 않기 위한 설계(설계 세션에서 확정, 메모리
  telegram-chat-agent-design). `telegram_chat_agent_enabled=false`로 재배포 없이 끌 수 있다
  (LLM 비용/예상 밖 동작 손잡이). 프롬프트 조립(도구 카탈로그 → 문자열)은 `domain/chat_agent.py`
  순수 함수라 포트 없이 테스트되고, `ApplicationRepository.list_recent`(신설, §11.2 포트)가
  "최근 지원 건 목록" 조회 공백을 메웠다. 도구 선택/응답 판단은 이력서 생성보다 훨씬 가벼운
  분류 작업이라 `c.llm`이 아니라 별도 `c.chat_llm`(같은 프로바이더, `TELEGRAM_AGENT_MODEL`
  기본값 Haiku)을 쓴다 — `bootstrap._build_llm`이 `model` 오버라이드 인자를 받아 프로바이더당
  모델이 다른 `LLMClient` 인스턴스를 두 개 만든다.
  **`start_applications`(2026-08-21, "상주 에이전트가 알아서 몇 건 지원해줘" 요청)**는 앞의
  두 도구와 달리 실제로 `ApplicationWorkflow`를 새로 "시작"한다 — 하지만 그 워크플로우 자체가
  제출 전 텔레그램 승인을 기다리게 돼 있어 절대규칙 4는 그대로 지켜진다("워크플로우를 안
  건드린다"가 아니라 "제출은 못 건드린다"가 진짜 불변식). 로직은 `apply_intake.py`(cli.py/
  watchdog.py처럼 Temporal Client SDK를 직접 쓰는 운영 진입점, 새 port 없음)에 있다:
  `uow.jobs.actionable()`(`JobCollectionWorkflow`가 Schedule로 채워둔 캐시)을 읽되, 요청마다
  라이브 재수집을 하면 느리고 rate limit도 갉아먹으므로 24시간 TTL(`JOB_CACHE_TTL`) 안의
  것만 후보로 삼는다(2026-08-21 사용자 결정) — 적합도(`fit_score`) 상위 N건을 고른다.
  `application_id`는 `domain.job_identity.canonical_key(company, title)`을 그대로 쓴다 —
  이 값이 원래 "중복지원 방어선"으로 설계돼 있어서(그 모듈 docstring), 같은 공고로 지원을
  이미 시작했으면 워크플로우 시작 자체가 막힌다. 다만 Temporal 기본
  `WorkflowIDReusePolicy.ALLOW_DUPLICATE`는 이전 실행이 COMPLETED로 끝난 뒤엔 같은 id 재시작을
  막지 않으므로, 이 호출에서만 명시적으로 `REJECT_DUPLICATE`를 줘서 "한 번이라도 시작한 공고는
  다시 시작 안 한다"를 강제하고 `WorkflowAlreadyStartedError`를 건너뛰기 신호로 쓴다.
  `dry_run` 인자(사용자 요청, 같은 날 추가)를 true로 주면 `client.start_workflow` 자체를 안
  부르고 선정 로직(TTL/정렬/count)이 뭘 골랐을지만 보여준다 — `DRY_RUN_ONLY`(§9.5, 실행 단계의
  제출 여부)와는 다른 레벨의 "dry run"이라 헷갈리지 않게 문서에 명시했다. 이 경로는 Temporal을
  안 건드리므로 `WorkflowAlreadyStartedError` 기반 중복지원 dedupe도 작동하지 않는다는 한계가
  있다(후보만 보여줄 뿐, 실제로 이미 지원했는지는 안 걸러진다) — 테스트/확인용이라는 전제.
  **재지원을 어디까지 허용하는가 (`_RETRYABLE_STATES`).** 후보 선정의 사전 필터는 기존 이력이
  있는 공고를 빼되, `REJECTED`/`NEEDS_HUMAN`/`EXPIRED` 셋은 완전히 배제하지 않는다 — 셋 다
  `submitted_at`이 None 인 채 끝나는 경로라 "이미 지원한 것"이 아니기 때문이다. 취급은 두
  갈래다: `REJECTED`는 사람의 명시적 거절이므로 신규 후보 **뒤로 순위만** 밀고,
  `NEEDS_HUMAN`/`EXPIRED`는 사람의 의사 표현이 아니라 사고·무응답이므로 신규 후보와 **완전히
  동등하게** `fit_score` 순서에 섞는다. `NEEDS_HUMAN`은 2026-08-22 사용자 요청("후순위로
  하지마")으로, `EXPIRED`는 "승인 대기 72시간 무응답으로 expired 되도 재지원되냐"는
  질문(2026-08-23)으로 갭이 드러나 뒤늦게 같은 집합에 들어왔다(`debbfa6`).

  이 필터와 **별개로** 시작 시점의 `WorkflowIDReusePolicy`가 또 한 겹이다. 여기서 한 번
  틀렸다 — `REJECT_DUPLICATE`로 걸어두면 "한 번이라도 시작한 공고는 다시 시작 안 한다"가
  강제되는데, 그게 위 재시도 허용과 정면으로 충돌해서 `NEEDS_HUMAN` 건을 텔레그램에서
  재시도해도 **조용히 아무 일도 안 일어났다**(`cc4fa68`). 지금은 `ALLOW_DUPLICATE`이고,
  중복 방어는 위 사전 상태 필터가 담당한다 — 두 겹이 서로 다른 규칙을 강제하면 약한 쪽이
  아니라 **엄한 쪽이 조용히 이긴다**는 게 이 버그의 교훈이다.

  **`apply_by_url`(2026-08-21, "wanted 링크 보내면 지원 프로세스 도는 기능 있냐"는 질문에서
  이어진 요청)**은 `start_applications`와 반대 방향이다 — 적합도로 자동 선정하는 대신 사람이
  URL을 직접 지정("이 링크 지원해줘")한다. `uow.jobs.actionable()` 캐시를 안 거친다(사람이
  이미 골랐으니 다시 스크리닝할 이유가 없다) — 대신 `c.registry.for_url(url)` +
  `adapter.fetch_job(url)`(둘 다 `PlatformAdapter` port, `ApplicationWorkflow`의
  `collect_job` activity가 쓰는 것과 같은 어댑터)로 즉석에서 `company`/`title`을 얻어
  `canonical_key`를 계산하고, 기존 이력(REJECTED 제외)이 있으면 시작하지 않는다 —
  `start_actionable_applications`의 사전 상태 필터와 같은 판정을 1건짜리로 반복한다.
  플랫폼은 wanted로만 한정한다(`apply_intake._APPLY_BY_URL_PLATFORMS`) — registry 자체엔
  saramin도 등록돼 있지만(`bootstrap._build_registry`), saramin은 자소서 문항 있는 공고가
  아직 라이브 검증이 안 끝나서(메모리 saramin-recipe-progress.md) 임의 링크를 사람 개입 없이
  실행 트리거하기엔 이르다는 판단(사용자 요청으로 명시적 제한). 워크플로우 조립(`StartApplication`
  구성 + `REJECT_DUPLICATE` dedupe) 자체는 `start_actionable_applications`의 루프와
  `_start_workflow` 헬퍼로 공유한다. 사람이 붙여넣은 원본 `url`을 그대로 워크플로우에 넘기고
  (`fetch_job`이 리다이렉트 등으로 정규화한 값이 아니라) — 나중에 워크플로우 상태를 봤을 때
  "이 링크로 시작했다"가 그대로 남게 하려는 선택이다.

  **`schedule_status`/`set_schedule_enabled`/`set_schedule_time`(2026-08-22, § 11.2f)**는
  앞의 셋과 달리 `ApplicationWorkflow`를 시작하지 않는다 — `apply_intake.py`가 채팅으로
  "지금 몇 건 지원"을 트리거하는 쪽이라면, 이 셋은 그 트리거를 cron 으로 매일 자동 실행하게
  등록해둔 두 Temporal Schedule(공고 수집·자동 지원)의 켜짐/꺼짐·시각·건수를 채팅으로
  본다/바꾼다. 시각·건수는 `.env`가 아니라 DB(`ScheduleConfig`)에 산다 — 채팅으로 바꾸면
  재배포·설정파일 수정 없이 바로 반영된다. 자세한 이유는 §11.2f.

  **ReAct 루프의 안전장치 (전부 코드, 프롬프트 아님).** 실측 사고 하나가 이 셋을 만들었다
  (2026-08-21): "2건정도 지원해줘"라고 했는데 `chat_llm`(Haiku)이 `start_applications(count=2)`의
  성공 관찰 결과를 보고도 `respond`로 안 끝내고 같은 도구를 계속 다시 불러, 한 턴에 4번 ×
  2건 = 8건이 실제로 시작됐다(직전 시작분은 이미 이력이 생겨 자동으로 건너뛰므로 매번 *다음*
  순위 공고를 골랐다). `count`로 한 번의 상한은 걸었지만 "그 도구를 몇 번 부르는지"는 아무도
  안 막고 있었던 게 진짜 구멍이었다.

  - **(도구, 인자) 완전 일치 재호출 차단** — 이미 부른 조합은 실제 실행 없이 관찰 결과만
    "이미 호출했다"로 돌려준다.
  - **`SINGLE_SHOT_TOOLS`** — `start_applications`는 인자가 달라도 한 턴에 1회만 실행된다.
    `apply_by_url`은 여기 안 넣었다 — "링크 두 개 지원해줘"처럼 url 이 매번 다른 반복 호출이
    정상 사용이라, 위의 완전 일치 차단만으로 충분하다.
  - **스텝 예산을 둘로 분리** — `MAX_STEPS`(8, 진전을 낸 스텝만)와 `MAX_BLOCKED_STEPS`(8,
    중복으로 차단된 스텝)를 따로 센다. 하나로 세면 중복 시도 몇 번에 예산이 소진돼서 도구를
    3개 이상 부르는 정상 요청까지 같이 죽었다.
  - **예산 소진 시 사과만 하지 않는다** — `build_fallback_message(executed)`가 그 턴에 실제로
    실행된 도구 결과를 같이 보여준다. "처리하지 못했습니다"만 보내면 사용자는 8건이 이미
    시작된 걸 모른다.
  - **transcript에 성공/실패/차단을 구분해 남긴다** — 전부 ✅로 뭉뚱그리면 모델이 실패한
    호출을 성공으로 읽고 그 위에 답을 쌓는다.

  루프는 어떤 경우에도 예외를 던지지 않고 사과 메시지로 끝난다 — 웹훅 라우트가 500을 내지
  않는다는 전제(§7)를 이 함수가 지킨다.

  **`retry_application(application_id)`(2026-08-23)** — claude CLI 한도초과로 `NEEDS_HUMAN`에
  떨어진 지원 건을, 한도가 풀린 뒤 재시도하려다 발견한 갭이다. `list_applications`가
  canonical_key 해시만 보여줘서 어떤 공고인지 알 수가 없었고, `apply_by_url`은 URL을 알아야
  하는데 해시에서 원 링크를 되찾을 방법이 없었다. `apply_intake.find_job_by_application_id`가
  `jobs` 저장소를 canonical_key 로 역스캔해 URL을 찾고, `apply_by_url` 과 같은 시작
  경로(`_start_workflow`)에 그대로 태운다.

  - **`jobs` 테이블 자체엔 TTL이 없다** — 24시간 TTL은 `start_actionable_applications`의 자동
    선정 로직에만 걸린 필터고, 저장소는 계속 들고 있다. 그래서 역조회는 오래된 건도 찾는다.
  - 별도 인덱스 없이 **선형 스캔**이다. 채팅에서 사람이 직접 트리거하는 저빈도 호출이라 감수했다.
  - 캐시에서 못 찾으면(오래돼 다른 공고로 덮어써짐 등) `apply_by_url`로 URL을 직접 달라고
    안내한다. 그 경우까지 구제하려면 application 레코드에 `job_url`을 영속해야 해서 스키마
    변경이 필요해지는데, 이번 범위를 넘는다고 보고 보류했다.
  - 같은 역조회로 `list_applications`도 회사/직무를 붙여 보여준다(캐시에 없으면 기존처럼
    application_id 만).

  **`collect_now()`(2026-08-23)** — 공고 수집을 Schedule 을 기다리지 않고 즉시 1회 돌린다.
  `JobCollectionWorkflow`를 Schedule 이 쓰는 것과 같은 플랫폼 목록으로 시작할 뿐이고, 수집만
  하고 지원은 시작하지 않는다(이어서 지원하려면 `start_applications`를 따로 부른다) — 한
  도구가 두 가지 되돌리기 어려운 일을 묶어서 하지 않게 갈라둔 것이다.
