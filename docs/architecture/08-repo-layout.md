# 리포지토리 구조

> `docs/ARCHITECTURE.md` 색인의 §8. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 8. 리포지토리 구조

실제 트리다(2026-08-24 기준). 계층 규칙은 `make arch`(import-linter)가 강제한다 — §11.7 체크리스트.

```
auto-apply-v2/
├── docker-compose.yml            postgres · temporal · temporal-ui · minio
├── alembic/versions/             applications/jobs/attempts/schedule_configs 마이그레이션
├── config/
│   ├── matching.yaml             하드컷/트랙/스코어링 규칙 — 사용자의 직무 취향 데이터 (§11.2b)
│   ├── facts.yaml                이력서 생성의 유일한 사실 원천 — 사람이 직접 채운다 (§2.3, §4).
│   │                             개인정보라 gitignore 대상. facts.example.yaml(형식만, git 추적)을
│   │                             복사해서 만든다 — .env.example 과 같은 패턴
│   ├── profile.yaml              이력서 헤더/학력/스킬태그/언어 — LLM 을 거치지 않는 정형 정보(§2.3)
│   ├── portfolio_map.yaml        직무 카테고리 → 포트폴리오 파일 매핑 (§2.3)
│   ├── credentials.example.json  CredentialSource 형식 (§2.4b, 동결)
│   └── resume_guide.{platform}.md  REVISE(general)가 patch 로 갱신하는 생성 가이드 (§2.2, §6)
├── scripts/
│   ├── save_auth_state.py        사람이 수동 로그인해 storage_state 저장 (§3)
│   ├── auto_login.py             본인 계정 자격증명으로 자동 재로그인 — CAPTCHA 만나면 중단 (§3)
│   ├── explore_platform.sh       recipe-builder 라이브 탐색용 오제출 방지 가드
│   └── migrate_*.py              file→postgres 이관 · recipe 버전 레이아웃 이관 (1회성)
├── var/                          gitignore. REPOSITORY=file / STORAGE=local 일 때의 데이터도 여기
│   ├── recipes/{platform}/{version}.json   AutomationRecipe (draft→candidate→active→deprecated)
│   ├── auth/{platform}.json                Playwright storage_state
│   └── applications/ jobs/ attempts/ checkpoints/ dom-snapshots/ resumes/
├── src/auto_apply/
│   ├── api/                      FastAPI — main.py · deps.py · schemas.py
│   │   └── routers/              applications.py · recipes.py · telegram.py (§7)
│   ├── telegram/                 ★ 운영 진입점 계층
│   │   ├── bridge.py             콜백/답장 → signal 또는 checkpoint 결정 (nonce 검증은 워크플로우)
│   │   ├── listener.py           getUpdates 롱폴링 — 공인 URL 없는 로컬 개발용
│   │   ├── agent.py              자유 텍스트 ReAct 루프 (스텝 예산 · 중복 호출 차단) (§6)
│   │   ├── _agent_tools.py       TOOLS 레지스트리 본체 + 조회/지원 도구
│   │   └── _agent_tools_{schedule,collect,resend,retry}.py   도구 묶음별 분리 (§6)
│   ├── workflows/                ★ contracts/ 와 domain/ 만 import 한다
│   │   ├── application.py        ApplicationWorkflow (§2.2)
│   │   ├── _execution.py         실행 + 실패 처리 + verify_submission (§2.2, §5)
│   │   ├── _revision.py          REVISE 재생성 · 가이드 patch 2차 승인 (§2.2)
│   │   ├── _repair.py            repair child 시작 + dedupe 예외 처리 (§2.4)
│   │   ├── _errors.py            ActivityError.cause.type 분기 헬퍼
│   │   ├── resume.py             ResumeWorkflow (§2.3)
│   │   ├── repair.py             AutomationRepairWorkflow (§2.4)
│   │   ├── job_collection.py     JobCollectionWorkflow (§11.2b)
│   │   ├── apply_intake.py       ApplyIntakeWorkflow (§11.2f)
│   │   └── ping.py               스모크
│   ├── activities/               ★ port 를 주입받는 activity 구현. 클래스의 all() 로 등록
│   │   ├── application.py  resume.py  guide.py  repair.py
│   │   ├── browser.py            execute_application (heartbeat 포함, §2.4c)
│   │   ├── job_collection.py     collect_platform_jobs (§11.2b)
│   │   ├── apply_intake.py       start_actionable_applications 래퍼 (§11.2f)
│   │   └── ping.py
│   ├── contracts/                ★ workflow-safe: pydantic/stdlib 만, 벤더 SDK 없음(temporalio 예외)
│   │   ├── dto.py                워크플로우 입출력 · DecisionRequest · ScheduleConfig
│   │   ├── recipe.py             AutomationRecipe · Action (§3)
│   │   ├── job.py                JobPosting · ScreeningVerdict · ApplicabilityVerdict (§11.2b)
│   │   ├── matching_config.py    TrackRule · HardcutRule · MatchingConfig (§11.2b)
│   │   ├── fact.py               Fact (§2.3, §4)
│   │   ├── profile.py            Profile · EducationEntry · LanguageEntry (§2.3)
│   │   ├── portfolio.py          PortfolioMap (§2.3)
│   │   ├── resume_content.py     AssembledResume — ResumeDraft.content 의 실제 모양 (§2.3)
│   │   ├── web_agent.py          WebAgentTask · WebAgentSession (§2.4b)
│   │   └── activity_defs.py      activity 인터페이스 stub (@activity.defn) — worker 에 등록 금지
│   ├── ports/                    ★ Protocol 정의. 구현을 import 하지 않는다 (전체 목록은 §11.2)
│   │   ├── llm.py  storage.py  notifier.py  repository.py  executor.py
│   │   ├── platform.py           PlatformAdapter (URL 단건 조회 — job_source 와 구분, §11.2b)
│   │   ├── job_source.py         JobSource (플랫폼 대량 수집, §11.2b)
│   │   ├── matching_config.py  facts.py  profile.py  portfolio.py  guide.py
│   │   ├── resume.py             ResumeGenerator / ResumeReviewer
│   │   ├── pdf.py  clock.py      PdfRenderer · Clock/IdGen
│   │   ├── recipe_source.py      RecipeSource — 버전 목록/승격 (§2.4)
│   │   ├── checkpoint_store.py   CheckpointStore (§2.4c)
│   │   ├── attachments.py        AttachmentManager (§11.2e)
│   │   ├── credentials.py        CredentialSource — activity 경계를 넘지 않는다 (§2.4b)
│   │   └── web_agent.py          WebAgentExecutor (§2.4b, 동결)
│   ├── adapters/                 ★ port 별 구현체. 서로를 모른다
│   │   ├── llm/                  anthropic.py · claude_code_cli.py (§11.2c) · stub.py
│   │   ├── storage/              s3.py · local.py · memory.py
│   │   ├── notifier/             telegram.py · console.py
│   │   ├── repository/           postgres.py · file.py · memory.py · models.py
│   │   ├── executor/             playwright.py · agent_browser.py · replay.py · _checkpoint.py
│   │   ├── platform/             wanted.py · saramin.py · fixture.py · registry.py
│   │   ├── job_source/           wanted.py · saramin.py · jasoseol.py · fixture.py · _http.py
│   │   ├── recipe/               jsonfile.py · memory.py
│   │   ├── checkpoint/           file.py · memory.py
│   │   ├── attachments/          wanted.py · fixture.py · registry.py
│   │   ├── credentials/          json_queue.py · static.py
│   │   ├── web_agent/            aside_cli.py · replay.py
│   │   ├── resume/               simple.py · _assemble.py (프레임워크 미도입 — §9.2)
│   │   ├── pdf/                  weasyprint.py · _template.py · stub.py (§2.3)
│   │   ├── facts/ profile/ portfolio/ guide/ matching_config/   각각 yaml/file + static 대역
│   │   ├── clock/system.py       Clock/IdGen — 테스트는 대역을 인라인으로 만든다
│   │   └── _wanted_auth.py · _saramin_auth.py   storage_state 쿠키 로더 공유
│   ├── ai/                       ★ 프레임워크 무의존: 순수 Pydantic + 문자열 함수 (§9.2)
│   │   ├── schemas.py            ResumeContentSchema · BlockBullets · RecipeDiffSchema ·
│   │   │                         GuidePatchSchema · AgentStep — LLM 구조화 출력 스키마
│   │   └── prompts.py            프롬프트 조립 (LLM 호출 자체는 adapters/ 쪽에서)
│   ├── domain/                   ★ 순수 도메인. 프레임워크/어댑터 무의존
│   │   ├── enums.py  errors.py   상태 기계 · 에러 분류(NON_RETRYABLE, §5)
│   │   ├── job_identity.py       정규화 · canonical_key (§11.2b)
│   │   ├── job_screening.py      축1 적합도: 하드컷 · 트랙 · 스코어링 (§11.2b)
│   │   ├── job_applicability.py  축2 지원가능성: blocker · requires · caution_documents (§11.2b, §6)
│   │   ├── resume_matching.py    select_relevant_facts · ground_check (§2.3)
│   │   ├── resume_blocks.py      group_facts_for_resume · select_relevant_blocks (§2.3)
│   │   ├── resume_cleanup.py     build_resume_filename · select_deletable (§11.2e)
│   │   ├── recipe_policy.py      Recipe 정책 검증 — 스키마 검증과 별도 (§3, §2.4)
│   │   ├── recipe_repair.py      classify_failure · bump_goto_timeout · build_candidate_recipe (§2.4)
│   │   ├── recipe_selector.py    resolve_selector — '{value}' 치환 (§3)
│   │   ├── agent_browser_selector.py  Playwright 확장 문법 → agent-browser 3갈래 분류 (§3)
│   │   ├── guide_patch.py        apply_patch — old 가 정확히 1번 매치될 때만 (§2.2)
│   │   ├── login_flow.py         detect_login_outcome — CAPTCHA/추가인증 감지 (§3)
│   │   ├── chat_agent.py         도구 카탈로그 → 프롬프트 조립 (§6)
│   │   ├── schedule_cron.py      build_cron(hour, minute) (§11.2f)
│   │   └── alerting.py           조용한 실패 판정: collection_alert · intake_alert (§11.2d)
│   ├── bootstrap.py              ★ composition root: 설정 → 구현체 조립 (§11.4)
│   ├── config.py                 pydantic-settings
│   ├── temporal_config.py        큐 이름 · 공통 RetryPolicy
│   ├── worker.py                 --queue {default|ai|browser}
│   ├── cli.py                    ★ 운영 진입점 — start/approve/reject/schedule/collect*/apply*/resend-pending
│   ├── schedule.py               Temporal Schedule 등록/삭제 (§11.2b, §11.2f)
│   ├── schedule_config.py        DB(ScheduleConfig) ↔ Temporal Schedule 연결 (§11.2f)
│   ├── apply_intake.py           actionable 캐시 → ApplicationWorkflow 시작 · URL/재시도 (§6, §11.2f)
│   ├── pending_decisions.py      승인 대기 워크플로우 전수 조회 + 일괄 재전송 (§11.2g)
│   ├── watchdog.py               워크플로우 능동 감시 — make watchdog (§11.2d)
│   ├── process_alerts.py         상주 프로세스 크래시 알림 (§11.2d)
│   └── resume_cleanup.py         플랫폼 첨부파일 정리 — make resume-cleanup (§11.2e)
└── tests/                        76개 파일
    ├── ports/                    ★ contract test: 모든 구현체에 동일 스위트 (§11.5)
    ├── workflows/                Temporal test env (시간 스킵 → 72h 대기 즉시 검증)
    ├── activities/ adapters/ api/ telegram/ domain/ contracts/ ai/ schemas/
    └── fixtures/                 고정 HTML · 녹화된 LLM 응답
```

**운영 진입점 계층**(`telegram/`·`cli`·`watchdog`·`worker`·`schedule*`·`apply_intake`·
`pending_decisions`·`process_alerts`·`resume_cleanup`)은 Temporal Client SDK 를 직접 써도 되고
`workflows`/`activities` 를 import 해도 된다. 이 층에 거는 계약은 **"어댑터를 직접 만들지
않는다"(bootstrap 경유)** 하나뿐이다 — 이유는 §11.7 체크리스트에 있다.
