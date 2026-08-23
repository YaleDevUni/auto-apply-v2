# 어댑터·운영 진입점 상세

> `docs/ARCHITECTURE.md` 색인의 §11.2c–§11.2f. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

### 11.2c `ClaudeCodeCliLLM` — API 키 종량제 대신 로컬 구독

`AnthropicLLM`은 `ANTHROPIC_API_KEY`로 Messages API 를 직접 부른다(종량제). 이 프로젝트는
개발자 본인이 이미 Claude Code 구독(Pro/Max)을 갖고 있어서, 같은 워크로드를 API 키 없이
그 구독으로 실행할 수 있으면 이력서 생성 비용이 0에 가까워진다 — `ClaudeCodeCliLLM`
(`adapters/llm/claude_code_cli.py`)이 그 경로다: 이 머신에 `claude login`(또는
`claude setup-token`)으로 로그인된 `claude` CLI 를 `asyncio.create_subprocess_exec` 로
headless 호출한다(`-p --input-format stream-json --output-format stream-json --verbose` —
캐시 브레이크포인트를 찍으려면 stdin 으로 콘텐츠 블록을 나눠 보내야 해서 평범한 `-p <문자열>`
대신 이 모드를 쓴다, 아래 "캐시" 참고).

**`--bare`를 안 쓰는 이유 (실측):** `claude --bare --help`에 "Anthropic auth is strictly
ANTHROPIC_API_KEY or apiKeyHelper... (OAuth and keychain are never read)"라고 명시돼 있다.
`--bare`는 하네스를 걷어내는 지름길처럼 보이지만 인증 경로 자체를 API 키 종량제로 강제해서,
이 어댑터가 피하려는 과금 방식으로 되돌아간다. 그래서 낱개 플래그로 직접 걷어낸다:

| 걷어내는 것 | 플래그 |
|---|---|
| 빌트인 툴(Bash/Read/Write/...) | `--tools ""` |
| MCP 서버 | `--strict-mcp-config` (`--mcp-config` 생략) |
| 스킬/슬래시 명령 | `--disable-slash-commands` |
| 프로젝트/사용자 `settings.json`, `CLAUDE.md` | `--setting-sources ""` |
| 기본 시스템 프롬프트(툴 설명 등 포함) | `--system-prompt <우리 문장>`(교체, append 아님) |
| 모델 자동 라우팅 분류기 호출 | `--model` 명시 (실측: 생략하면 `modelUsage`에 `claude-haiku-4-5` 가 추가로 잡힌다 — CLI 가 라우팅용으로 Haiku 를 한 번 더 부른다) |

구조화 출력은 Anthropic Messages API 의 `tool_choice` 강제 대신 `--json-schema <JSON Schema>`를
쓴다 — `--output-format stream-json`의 마지막 줄(`"type":"result"`, 세션 없는
`--output-format json` 한 방 호출과 같은 모양)의 `structured_output` 필드에 스키마를 만족하는
값이 이미 파싱되어 온다(실측: `ResumeContentSchema`의 `$defs`/`$ref` 포함 중첩 스키마로 검증
완료). `structured_output`이 없거나 우리 Pydantic 모델 검증에 실패하면 `AnthropicLLM`과 같은
계약으로 `LLMSchemaViolation`을 던져서, `SimpleResumeGenerator`의 재프롬프트 루프(§2.3)가
그대로 재사용된다. 프로세스 자체가 실패(비정상 종료·타임아웃·JSON 파싱 실패·`is_error`)하면
`LLMExecutionError`— 스키마 문제가 아니라 대부분 일시적이라 재시도 대상이다(NON_RETRYABLE
에 없음).

**실패 분류 → 텔레그램 알림 (재시도로 안 풀리는 두 가지):** `is_error` 응답 중 일부는 재시도해도
똑같이 실패한다 — 로그인이 풀렸거나(`claude login` 필요) 구독 사용량 한도(5시간/주간)를
넘었을 때다. `_run()`은 exit code 를 먼저 보지 않고 stdout 을 먼저 JSON 파싱한다(실측: CLI 는
이 두 실패도 exit code 1 과 함께 stdout 에 유효한 JSON 을 낸다 — 로그인 풀림은
`result:"Not logged in · Please run /login"`, 한도초과는
`terminal_reason:"budget_exhausted"`/`subtype:"error_max_budget_usd"`). 그 문자열/필드를
`_classify_error()`가 CLI 바이너리 안에 실제로 박혀 있는 auth-실패 감지 정규식과 같은 패턴으로
분류해서 `LLMAuthRequired`/`LLMQuotaExceeded`(둘 다 `LLMExecutionError`의 서브클래스,
`domain/errors.py`)를 던진다 — 이 둘은 NON_RETRYABLE 이라 Temporal 이 재시도 없이 1회만
시도한다. `ResumeWorkflow`가 `generate_resume`/`review_resume` 호출을 감싸고 `ActivityError.cause.type`
으로 이 둘을 알아보면(§ CLAUDE.md "Temporal 관련 주의" — `.type` 문자열 비교), 재던지기 전에
`notify` activity(`ports/notifier.py`, 이미 승인 흐름이 쓰는 것과 같은 채널)를 큐를 건너
(`task_queue=QUEUE_DEFAULT`, `render_pdf`가 반대 방향으로 `QUEUE_AI`를 넘기는 것과 대칭) 호출해
사람에게 알린다. NON_RETRYABLE 이라 시도가 정확히 1번이라 알림도 자연히 1번만 나가고, 별도
debounce 는 안 뒀다.

**캐시 (사용자 요청 "cache 적극 활용", 재설계 기록: [[claude-cli-prompt-cache-redesign]]):**
처음엔 세션(`--resume <session-id>`)을 이어야만 캐시가 붙는다고 실측했지만, 그건 "프롬프트
앞부분만 같고 뒷부분이 매번 달라지는" 케이스에서 세션 없이는 캐시가 하나도 안 붙는다는
것만 확인한 결과였다. 다시 실측해보니 진짜 원인은 세션 유무가 아니라 **콘텐츠 블록을 안
나눈 것**이었다 — `-p <문자열>`로 프롬프트 전체를 하나의 블록으로 보내면, 앞부분이 바이트
단위로 완전히 같아도 뒷부분이 한 글자만 달라지는 순간 그 블록 전체가 캐시 미스가 된다(부분
프리픽스 매칭이 전혀 안 됨 — 실측: 27482 토큰짜리 공통 접두어 + 서로 다른 20자 접미어인 두
번의 세션 없는 호출이 각각 독립적으로 `cache_creation`만 찍고 `cache_read`는 0이었다).
반대로 `--input-format stream-json`으로 안정적인 부분과 매번 바뀌는 부분을 **별도 텍스트
블록**으로 나누고 안정적인 블록에만 `cache_control:{"type":"ephemeral","ttl":"1h"}`을 찍으면,
세션을 전혀 안 이어도(`--no-session-persistence` 그대로) 완전히 독립된 두 번째 프로세스가
그 블록을 `cache_read`로 읽었다(실측: 27478 토큰 `cache_creation` → 다음 호출
`cache_read_input_tokens=27420` + 새 접미어만 `cache_creation=52`, 비용 $0.102 → $0.024).

그래서 세션 재사용(`--session-id`/`--resume`, `_MAX_TURNS_PER_SESSION`/`_SESSION_TTL_SECONDS`,
키별 `asyncio.Lock`) 기반 설계는 걷어냈다. `LLMClient.structured()`/`complete()`의 선택
파라미터를 `cache_key`(세션 재사용 힌트)에서 `cache_prefix: str = ""`(안정적인 접두어 문자열
그 자체)로 바꿨다 — 실제로 모델에 보내는 내용은 `cache_prefix + prompt`다. `StubLLM`은 무시,
`AnthropicLLM`은 `cache_prefix`가 있을 때만 그 부분을 별도 블록으로 떼어 `cache_control`을
붙인다. `ClaudeCodeCliLLM`도 같은 신호로 `cache_prefix`가 있으면 2블록(`cache_prefix`엔
브레이크포인트, `prompt`엔 없음), 없으면 1블록 메시지를 만들어 stdin 으로 넘긴다 — 매 호출이
독립 프로세스이므로 세션 상태/락이 필요 없어졌다.

이 파이프라인에서 바이트 단위로 진짜 안정적인(=서로 다른 공고에 걸쳐서도 100% 동일한) 유일한
구간은 **한 번의 `generate()` 호출 안에서의 재프롬프트 시도들**이다 — `select_relevant_facts`가
공고 설명으로 Fact 를 필터링해서 서로 다른 공고끼리는 프롬프트가 애초에 다르다. 그래서
`SimpleResumeGenerator._structured_with_reprompt`는 원본 프롬프트를 `cache_prefix`로 고정하고
매 시도의 `prompt`엔 재시도 여부에 따라 빈 문자열이거나 `reprompt_error_suffix()`(오류 안내문)
만 담는다 — 스키마 위반으로 재프롬프트가 걸리면 2·3번째 시도가 원본을 `cache_read`로 읽는다.
사용자 단위 세션(`cache_key=req.user_id`)이 갖고 있던 오염 위험(이전 공고의 대화가 다음 공고
생성에 섞여 들어가는 것)도 세션 자체를 없애면서 구조적으로 사라졌다. `GuideActivities.
propose_guide_patch`처럼 재시도 루프가 없는 단발 호출은 재사용할 캐시 경계가 없어
`cache_prefix`를 안 넘긴다.

`LLM_PROVIDER=claude_cli` + `CLAUDE_CLI_BINARY`/`CLAUDE_CLI_MODEL`/`CLAUDE_CLI_MAX_BUDGET_USD`로
켠다(`.env.example`). 기본값은 여전히 `stub`이고, `anthropic`도 그대로 남아있다 — 어느 걸 켤지는
사용자 몫이다(§11.6과 같은 이유로 자동 전환하지 않는다). worker 를 띄우는 머신에 `claude` CLI 가
설치되고 로그인돼 있어야 한다는 전제가 있어 CI/컨테이너 배포 환경에는 안 맞을 수 있다 — 로컬
개발/개인 실행 용도다.

### 11.2d 워크플로우 능동 감시 — `watchdog.py`

`_execute()`의 `load_active_recipe`가 try/except 없이 흘러 `ApplicationWorkflow`가 조용히
FAILED로 죽었던 사고(workflow-failure-visibility-backlog, `de56dcd`)의 1차 픽스는 그 지점을
workflow 코드 안에서 잡아 `NEEDS_HUMAN`으로 정상 종료시키는 것이었다 — 이게 Temporal
커뮤니티의 표준 권고이기도 하다: "워크플로우 실패를 감지하려면 워크플로우 안에서
잡아라"(maxim, [Temporal Forum](https://community.temporal.io/t/sending-notification-when-the-workflow-has-failed/14701)).
하지만 이건 *알고 있는* 실패 지점에만 통한다. 앞으로 또 생길 수 있는 코드 버그, 사람의 실수로
인한 `terminate`, `workflow_execution_timeout`처럼 워크플로우 코드가 아예 더 못 도는 종료까지
잡으려면 프로세스 밖에서 감시하는 수밖에 없다 — 이것도 커뮤니티에서 "실시간은 아니지만
유일한 외부 감지 수단"으로 확인했다([Forum](https://community.temporal.io/t/is-it-possible-to-listen-for-workflow-failures/6843)).

그래서 `watchdog.py`는 `cli.py`/`schedule.py`와 같은 부류의 **운영 진입점**(workflow 파일이
아니므로 §11.3 규칙 대상이 아니다)으로, Temporal Client의 visibility API(`list_workflows`)를
직접 폴링한다. Elasticsearch 없는 이 스택(Standard/SQL visibility, docker-compose)도
`ExecutionStatus IN (...) AND CloseTime > ...` 쿼리를 지원해서
([List Filter 문서](https://docs.temporal.io/list-filter)) 별도 검색 인프라 없이 충분하다.
새 port를 만들지 않았다 — 알림은 이미 있는 `Notifier` port(`notify` activity와 같은 이벤트
모양, kind=`WORKFLOW_UNHEALTHY`)를 그대로 쓴다.

재시작 사이의 워터마크(마지막으로 확인한 시각)를 영속화하지 않는다 —
`telegram/listener.py`의 offset과 같은 트레이드오프다. 재시작 직후엔
`WATCHDOG_LOOKBACK_MINUTES`(기본 60분)만큼 과거를 다시 훑어서 최대 그 창 안에서 중복 알림이
날 수 있는데, 감시의 존재 이유 자체가 "아무도 안 보고 있을 때" 대비라 놓치는 것보다 몇 번 더
알리는 쪽이 훨씬 싸다. `WATCHDOG_POLL_INTERVAL_SECONDS`(기본 60초)로 폴링 주기를 조정한다.
`make watchdog`으로 띄운다 — 워커/리스너처럼 상시 프로세스다.

**폴링 대신 서버 push는 검토했으나 보류.** Temporal 서버에 워크플로우 종료(`WorkflowClosed`)
시 서버가 직접 HTTP로 콜백을 쏘는 `completion_callbacks` 메커니즘이 실제로 존재한다(proto
`Callback.Nexus` variant, `CallbackInfo.Trigger.WorkflowClosed`) — 죽은 워크플로우가 스스로
알리는 게 아니라 서버가 상태 전이를 감지해서 보내는 것이라 "코드 안에서 못 잡는 종료"도
원리적으로 커버할 수 있다. 설치된 `temporalio==1.31.0`의 `Client.start_workflow(callbacks=...)`
로 실제로 호출 가능한 것까지 코드 레벨(`client/_client.py`, `client/_impl.py`,
`nexus/_operation_context.py`)로 확인했다. 그럼에도 안 쓰기로 한 이유:
(1) SDK가 이 파라미터를 `start_workflow` 타입 오버로드에서 일부러 빼고 "public API 아님,
하위호환 보장 안 함"이라 주석에 명시 — Nexus worker 내부 배관용이지 애플리케이션이 쓰라고
낸 표면이 아니다. (2) 서버 쪽 dynamic config(`component.callbacks.allowedAddresses` 등)로
콜백 주소를 whitelist해야 하고, 공식 문서도 Nexus 오퍼레이션 문맥으로만 이 기능을 설명한다.
(3) 콜백이 실제로 어떤 payload로 오는지(Nexus completion 프로토콜 포맷 추정) 검증 못 했다.
안정적으로 보장된 visibility API 폴링을 두고 비공식·불안정 표면으로 갈아탈 이유가 없다는
판단이다 — 나중에 같은 질문이 또 나오면 이 문단으로 답할 것.

**watchdog이 못 보는 네 가지, 그리고 그 보완** (2026-08-22 전수 조사). watchdog은 "Temporal이
FAILED/TERMINATED/TIMED_OUT으로 **닫은** 워크플로우"만 본다. 조사해보니 그 정의 밖에서
조용히 실패하는 지점이 네 부류 있었다 — 전부 로그에만 남고 텔레그램은 울리지 않았다.

1. **성공으로 끝나는 실패** — `JobCollectionWorkflow`는 플랫폼 하나가 죽어도 나머지를
   살리려고 예외를 결과 필드(`PlatformCollectionResult.error`)로 삼키고 COMPLETED로 끝난다.
   셀렉터가 바뀌어 `found=0`이 되는 경우는 예외조차 안 난다. `ApplyIntakeWorkflow`도 후보
   0건이면 아무것도 시작하지 않고 정상 종료한다. 둘 다 cron으로 도는 루틴이라 "아무 일도
   안 일어난 것"과 구별이 안 된다. → 판정을 순수 함수 `domain/alerting.py`
   (`collection_alert`/`intake_alert`)로 두고, 두 워크플로우가 끝에서 `notify` activity를
   건다(kind=`JOB_COLLECTION_UNHEALTHY`/`APPLY_INTAKE_UNHEALTHY`). 판정을 워크플로우 코드가
   아니라 domain에 둔 건 Recipe·가이드 patch와 같은 이유다 — 임계치를 바꿀 때 Temporal 없이
   테스트로 고정할 수 있어야 한다. 알림 전송 실패가 수집 결과나 원래 예외를 덮지 않게
   `notify` 호출은 각 워크플로우에서 try/except로 감싼다.
2. **상주 프로세스의 죽음** — worker가 죽으면 워크플로우는 FAILED가 아니라 **Running인 채로
   멈춘다**(watchdog은 닫힌 것만 보므로 영영 못 잡는다). listener가 죽으면 승인 버튼이 그냥
   안 먹고 `approval_timeout`(기본 72시간) 뒤에야 EXPIRED가 된다. watchdog 자신이 죽으면
   감시가 사라진다. → `process_alerts.run_guarded(name, main)`이 세 프로세스의 `main()`을
   감싸 크래시 시 알리고 그대로 재던진다(kind=`PROCESS_CRASHED`). `SystemExit`(설정 오류로
   인한 조기 종료)/`KeyboardInterrupt`(사람이 껐다)는 사고가 아니라 알리지 않는다.
3. **감시가 눈이 먼 구간** — Temporal 접속이 끊기면 watchdog의 폴링이 계속 실패하는데,
   프로세스는 살아 있으므로(그게 맞다 — 감시가 감시 대상이 되면 안 된다) 그 침묵이 "아무
   문제 없음"으로 읽힌다. → `blind_alert`가 `WATCHDOG_BLIND_ALERT_AFTER`(기본 3회) 연속
   실패에 **정확히 한 번** 알리고(계속 실패하는 동안 매 주기 알리면 그게 소음이다), 복구되면
   `WATCHDOG_RECOVERED`를 보낸다.
4. **인바운드 처리 실패** — 리스너의 dispatch가 예상 밖 예외로 죽으면 로그만 남고 버튼을
   누른 사람에겐 무응답으로 보인다(`_notify_signal_failed`가 다루는 "signal이 안 닿았다"와
   증상이 같아 원인 구분이 불가능하다). 웹훅 경로도 500이 나면 텔레그램 서버만 알고 사람은
   모른다. → 문구를 `telegram/bridge.inbound_failure_message` 하나로 공유하고, 롱폴링은
   `_process_updates(..., on_error=)`로, 웹훅은 `api/main.py`의 미처리 예외 처리기
   (kind=`API_ERROR`)로 각각 알린다.

`process_alerts.py`는 `cli.py`/`watchdog.py`/`apply_intake.py`와 같은 **운영 진입점** 계층이다
— 새 port를 만들지 않고 `Notifier`를 그대로 쓴다(§11.6). `notify_safely()`가 모든 전송을
감싸는데, 알림을 보내는 자리는 대부분 이미 뭔가 잘못된 지점이라 거기서 알림이 또 터지면 원래
오류가 traceback에서 가려지기 때문이다.

### 11.2e 플랫폼 첨부파일 정리 — `AttachmentManager` / `resume_cleanup.py`

Recipe는 지원마다 `resumes/{filename}.pdf`로 이력서를 **새로** 렌더링해 업로드한다 — 과거에
올린 파일을 재사용하는 경로가 없다. 그 결과 지원(dry_run 포함) 1회 = 플랫폼 계정에 영구히
남는 고아 파일 1개다.

파일명은 `domain/resume_cleanup.build_resume_filename(name, resume_id)`가 짓는다 —
`{이름}_이력서_{resume_id의 16-hex}.pdf`(예: `박예일_이력서_2942bf8c75c249e7.pdf`).
원래 `res_<16-hex>.pdf`처럼 내부 ID를 그대로 노출하는 이름이었는데, 채용담당자가 원티드
업로드 목록에서 파일명만 보고 이력서인지 포트폴리오인지 구별할 수 없다는 문제(실사용
피드백, 2026-08-20)로 사람이 읽을 수 있는 접두부(`{이름}_이력서_`)를 붙이고 뒤에 해시
접미사를 남겼다 — 이 해시가 `_GENERATED_RESUME` 정규식이 "자동 생성물"만 골라 지우는
근거라, 이름을 짓는 함수와 정규식은 같은 파일에 두고 같이만 바꾼다.
실측(2026-08-20, agent-browser 라이브 탐색으로 wanted `/cv/list` 확인): 이 축적이 실제
문제였고(wanted-resume-list-cleanup-backlog), wanted는 `DELETE
/api/chaos/resumes/v1/{key}` 삭제 API를 제공하며 **쿠키 인증만으로** 동작한다(Authorization
헤더·localStorage 토큰 불필요) — `PlaywrightExecutor`가 쓰는 것과 같은
storage_state(`var/auth/wanted.json`)를 httpx 로 그대로 재사용하면 되고, 브라우저를 새로
띄울 필요가 없다.

`PlatformAdapter`(공고 조회/지원 실행, §11.2)와는 다른 축이라 새 port
`AttachmentManager`(`list_attachments`/`delete_attachment`, `StaticAttachmentRegistry`로
등록 — `PlatformRegistry`와 같은 allowlist 패턴)를 만들었다. 판정은 순수 함수
`domain/resume_cleanup.select_deletable`이 한다 — `{이름}_이력서_<16-hex>.pdf` 패턴(또는
알려진 테스트 산출물 `recipe-test-dummy.pdf`)에 맞는 `application/pdf` 만 대상이다. 포트폴리오
파일(`config/portfolio_map.yaml`, 고정 파일명으로 여러 지원에 재선택됨)과 사람이 직접 올린
이력서, `content_type == "wanted/resume"`(이 프로젝트가 만들지 않는 원티드 자체 이력서
빌더 문서)는 이름이 패턴에 안 맞아 자동으로 보존된다.

`application_id` ↔ wanted 파일 사이의 상관관계는 DB에 없다(`application_attempts`가 업로드한
이력서 파일명을 기록하지 않는다 — 확인됨) — 그래서 "이미 지원 완료된 것만" 지우는 대신 age
버퍼(기본 2시간, `--min-age-hours`)로 "혹시 아직 실행 중인 워크플로우가 쓰고 있을 최근 파일"을
보호한다.

`resume_cleanup.py`는 `watchdog.py`처럼 Temporal Client SDK를 직접 쓰는 **운영
진입점**(workflow 파일이 아니므로 §11.3 대상 아님)이지만, watchdog와 달리 Temporal 자체가
필요 없다(워크플로우 상태를 안 보고 플랫폼 API만 친다) — `make resume-cleanup`으로 1회
실행한다. 상시 폴링 프로세스가 아니다: 삭제는 되돌릴 수 없는 행위라 사람이 그때그때 후보
목록을 보고 판단하는 쪽을 택했다(CLAUDE.md "되돌릴 수 없는 행위는 사람 승인 뒤에서만" —
여긴 텔레그램 승인 대신 명시적 `--yes` 플래그가 그 역할). 기본은 dry-run(후보만 출력).

### 11.2f 자동 지원 시작 Schedule — `ApplyIntakeWorkflow` + 텔레그램 on/off

"공고수집 및 지원하기 스케줄링 기능 봇에 탑재해"(2026-08-22) 요청으로 신설. 공고 수집은
이미 §11.2b의 Schedule로 주기 실행됐지만, "지원 시작"(`apply_intake.start_actionable_applications`,
채팅 도구 `start_applications`가 쓰는 그 함수)은 사람이 매번 채팅으로 트리거해야 했다 —
이번 요청은 그 자리도 cron 으로 채워달라는 것.

**"자동 지원"이 실제로 자동화하는 범위.** `JobCollectionWorkflow`가 채운 actionable 공고
캐시에서 적합도 상위 N건에 대해 `ApplicationWorkflow`를 새로 **시작**하는 것까지만 자동이다
— 이력서 생성 → 텔레그램 승인 대기 진입까지다. 최종 제출은 그렇게 시작된
`ApplicationWorkflow` 안에서 여전히 사람의 텔레그램 승인 뒤에만 일어난다(CLAUDE.md
절대규칙 4). `apply_intake.py`의 기존 docstring이 이미 짚었듯 진짜 불변식은 "워크플로우를
안 건드린다"가 아니라 "제출은 못 건드린다"이고, 이 Schedule 도 그 경계를 그대로 넘겨받는다.

**새 워크플로우가 필요했던 이유.** Temporal Schedule은 워크플로우만 시작할 수 있는데,
`start_actionable_applications`(TTL 캐시 조회 → dedupe → `ApplicationWorkflow` 여러 건
시작)는 지금까지 workflow 코드가 아니라 `cli.py`/텔레그램 채팅 도구가 쓰는 **운영
진입점**이었다(Client를 직접 들고 I/O를 한다 — §11.3 규칙 대상이 아니다). 이 함수를
그대로 재사용하되 Schedule 이 시작할 수 있는 형태로 감싸는 얇은 계층 하나만 새로 추가했다:

```
ApplyIntakeWorkflow.run(ApplyIntakeInput)
  → activity: start_actionable_applications(cmd) (ApplyIntakeActivities)
      → apply_intake.start_actionable_applications(cmd.count, container, client)  # 기존 함수 그대로
```

`ApplyIntakeActivities`는 다른 activity 들(개별 port 주입)과 다르게 `Container`를 통째로
받는다 — `start_actionable_applications`가 CLI·채팅 도구·이 activity 세 호출부에서 정확히
같은 시그니처를 유지하길 원해서고, 포트별로 쪼개면 호출부마다 다시 조립해야 한다(activities
계층이 `bootstrap.Container`를 직접 아는 게 유일한 예외 — import-linter 계약도 activities가
adapters를 *직접* import하는 것만 막지 bootstrap 자체는 막지 않는다, §11.7 체크리스트
"어댑터 생성은 bootstrap 에서만"이 `allow_indirect_imports=True`인 것도 같은 이유). 이
activity가 내부에서 여러 `ApplicationWorkflow`를 시작하는 것 자체는 Temporal의 표준 패턴은
아니지만(보통 child workflow), sibling workflow 를 `WorkflowIDReusePolicy.REJECT_DUPLICATE`
로 dedupe 하는 기존 설계(§ apply_intake.py, telegram-chat-agent-design 메모리)와 맞추려면
child workflow 로 묶을 이유가 없었다 — activity 자체가 이미 실패해도 안전한 재시도
단위다(개별 시작은 `WorkflowAlreadyStartedError`로 걸러지므로 전체를 재시도해도 중복
시작되지 않는다).

**Schedule 등록/갱신/삭제.** `schedule.py`의 `build_apply_intake_schedule`/
`ensure_apply_intake_schedule`/`delete_apply_intake_schedule`가, §11.2b의 job-collection
Schedule과 완전히 같은 모양(create-or-update, `SchedulePolicy(overlap=SKIP)`)을 반복한다.
`cron: str`/`count: int`를 인자로 그대로 받을 뿐 `Settings`를 보지 않는다 — 아래 문단대로
그 값의 원천이 `.env`에서 DB로 옮겨가면서, 이 모듈이 굳이 그 출처를 알 이유가 없어졌다.

**시각/건수를 채팅으로 바꾼다 — DB 테이블이 원천, `.env`는 최초 시드일 뿐.** 처음엔
"채팅으로 cron 표현식을 파싱시키면 실패 위험이 크다"는 이유로 cron/건수를 `.env`에
고정하고 봇은 on/off 스위치만 쥐게 했는데, 구현 직후 사용자가 바로 정정했다 — "cron으로
하지 말고 서버에서 하면 설정파일 건드릴 필요도 없지 않냐"(2026-08-22). 정정된 요구는
"LLM이 cron 문법을 만들면 안 된다"는 우려 자체는 그대로 인정하면서(그래서 `hour`/`minute`
정수는 여전히 채팅에서 온다), "그 값을 `.env` 파일에 저장해서 재배포/재시작을 강제하지
말라"는 것이었다 — 그래서 새 `ScheduleConfig`(`contracts/dto.py`) + `ScheduleConfigRepository`
port(target당 최신값 1행, `ports/repository.py`의 `UnitOfWork.schedule_config`)를 만들어
memory/file/postgres 세 구현(`adapters/repository/*.py`, `schedule_configs` 테이블 —
`alembic/versions/05f35d5ad3d7_...`)을 얹었다. `domain/schedule_cron.build_cron(hour,
minute)`(순수 함수)가 여전히 "AI는 정수만, cron 조립은 코드" 경계를 지킨다 — 달라진 건
그 결과값을 어디 저장하느냐뿐이다.

`schedule_config.py`(운영 진입점, `cli.py`/`watchdog.py`와 같은 층)가 DB와 `schedule.py`를
잇는다: `load_or_seed(c, target)`는 DB에 값이 있으면 그대로, 없으면 `.env`
(`JOB_COLLECTION_CRON`/`APPLY_SCHEDULE_CRON` 등)로 만들어 저장한 뒤 돌려준다 — `.env`는
그래서 "DB가 한 번도 안 채워졌을 때의 최초 기본값"으로만 남는다. `save_and_push(c, client,
config)`는 DB에 먼저 쓰고 나서 `schedule.py`의 `ensure_*_schedule`로 Temporal에 반영한다
(순서가 중요하다 — 재시작해도 DB 값이 남아야 한다). `cli.py collect-schedule`/`apply-schedule`
은 `ensure(c, client, target)`(load_or_seed + push)로 재구현했다 — 처음 배포할 때 한 번
실행해 DB를 채우는 용도로만 남았고, 그 뒤로는 다시 실행할 필요가 없다.

**봇 도구 세 개** (`telegram/_agent_tools_schedule.py`, `TOOLS`에 병합 — `_agent_tools.py`가
이미 200줄을 넘어가서 분리, § CLAUDE.md "한 파일 = 한 책임"):
`schedule_status`(두 Schedule의 켜짐/꺼짐·시각·다음 실행 시각·건수/플랫폼을 본다),
`set_schedule_enabled(target, enabled)`(시각/건수는 그대로 두고 on/off만 — 끄기는
`ScheduleHandle.pause()`, 켜기는 `ensure()`로 DB/시드값을 다시 심고 `unpause()`한다),
`set_schedule_time(target, hour, minute, count)`(DB에 쓰고 즉시 Temporal에 반영 — `.env`도
재배포도 필요 없다). target=collection의 `platforms`는 여전히 채팅으로 못 바꾼다(범위를
의도적으로 좁혔다 — 사용자가 요청한 두 레버는 "몇시 몇건"이었다).

**실측 함정 둘.**
1. (2026-08-22, 라이브 Temporal 서버로 등록 후 `describe()` 확인) 서버는
   `ScheduleSpec.cron_expressions`를 등록 시점의 표현식 그대로 돌려주지 않는다 — 내부
   캘린더 스펙으로 컴파일하며 이 필드를 비워버린다. DB를 원천으로 옮기면서 자연히 해소됐다
   — `schedule_status`는 이제 cron을 Temporal에 아예 안 묻고 DB 값을 그대로 보여준다.
   `next_action_times`(다음 실행 시각)는 서버가 정상적으로 계산해 돌려주므로 그건 여전히
   Temporal에서 읽는다.
2. `ScheduleUpdate(schedule=schedule)`의 `schedule`은 매 호출 새로 지은 객체라
   `state.paused` 기본값이 `False`다 — 그래서 `handle.update()`만 부르면 꺼둔 Schedule이
   시각/건수만 바꿔도 조용히 다시 켜진다. `set_schedule_time`이 잦아지면서(전엔 온/오프
   토글만 있어 덜 드러났다) 실측으로 발견 — `schedule.py._create_or_update`가 update 전
   `paused` 여부를 읽어두고, 꺼져 있었으면 update 뒤에 다시 `pause()`해 복원한다
   (`tests/test_schedule.py::test_ensure_update_preserves_paused_state`가 실제 Temporal로
   이 회귀를 고정한다).

### 11.2g 승인 대기 일괄 재전송 — `pending_decisions.py`

텔레그램 리스너(`telegram/listener.py`)가 SIGTERM으로 죽어 있던 동안 워크플로우는 계속 승인을
기다린다 — 그 사이 눌린 버튼은 리스너가 못 받았을 뿐, 워크플로우 쪽 nonce는 그대로 살아있다.
리스너를 다시 띄운 사람의 문제는 "어떤 지원 건이 대기 중인지부터 모른다"는 것이다. 기존
`resend_pending_decision` 도구는 `application_id`를 미리 알아야 해서 이 상황을 못 구제했다.

`find_pending_decisions`가 Temporal visibility API를 `WorkflowType = 'ApplicationWorkflow'
AND ExecutionStatus = 'Running'`으로 훑어 실행 중인 지원 워크플로우를 전부 모으고, 각각의
`pending_decision` query가 `has_pending`인 것만 남긴다. `resend_all`이 그걸 전부 재전송한다.
**§11.2d의 watchdog과 방향이 정반대다** — watchdog은 *닫힌*(FAILED/TERMINATED/TIMED_OUT)
워크플로우를 찾고, 이건 아직 *RUNNING*인 것 중에서 고른다.

- **대기 여부는 워크플로우 자신만 안다.** `pending_decision` query가 유일한 소스이고 여기서
  다시 추정하지 않는다 — DB의 `applications.status`는 projection이라(§4.1) nonce를 갖고 있지
  않다.
- **원래 승인 메시지를 그대로 다시 보낸다** (2026-08-23). 최초 구현은 `application_id`(해시)
  한 줄만 보냈는데, 목록만 봐서는 뭘 승인하는지 알 수 없다는 지적을 받았다 —
  `TelegramNotifier.resend_decision`이 `request_decision`과 같은 렌더링(모드 배지·공고 링크·
  PDF 첨부·주의사항)을 재사용한다.
- **`resend_decision`은 `Notifier` port 표면에 없다** — Telegram 전용이다(§11.6 "Telegram이라는
  단어를 모르는 port"). 그래서 여기서도 구조적 Protocol(`ResendableNotifier`,
  `@runtime_checkable`)로만 가리킨다 — `telegram/bridge.py`의 `_RevisableNotifier`와 같은 패턴.
  호출측이 `c.settings.notifier == "telegram"`을 먼저 걸러야 한다는 불변식도 동일하다.

`cli.py resend-pending`(NOTIFIER=telegram 이 아니면 안내만 하고 끝)과 채팅 도구
`resend_all_pending_decisions`(§6)가 같은 로직을 공유한다. 테스트는 fake client 유닛 테스트 +
**`WorkflowType`/`ExecutionStatus` visibility 필터가 실제로 동작하는지 확인하는 실제 Temporal
서버 통합 테스트**(watchdog의 실측 테스트와 같은 이유로 `start_local()`,
`@pytest.mark.temporal`) 둘 다 있다 — 이 필터는 문서만 보고 맞다고 가정할 수 없는 부분이다
(§11.2b "테스트 함정"과 같은 교훈).
