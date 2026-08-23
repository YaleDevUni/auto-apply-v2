# auto-apply v2

> **Temporal로 상태를 맡기고, LLM에게는 생성만 시키며, 되돌릴 수 없는 행위 앞에는 반드시 사람을 세우는 구직 지원 자동화 파이프라인입니다.**
>
> 채용 공고를 주기적으로 수집해 적합도를 판정하고, 지원자의 검증된 사실(Fact)만으로 공고별 맞춤 이력서를 생성한 뒤 PDF로 렌더링하고,
> Telegram으로 승인을 받아 예약된 시각에 Playwright가 실제 지원 폼을 채웁니다.
> 사이트 DOM이 바뀌어 실행이 깨지면 LLM이 수선안(Recipe diff)을 제안하고, 샌드박스 검증과 사람의 승격 승인을 거쳐 다시 실행됩니다.

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![Temporal](https://img.shields.io/badge/Temporal-Durable_Workflow-000000)](https://temporal.io)
[![Playwright](https://img.shields.io/badge/Playwright-1.49-2EAD33)](https://playwright.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)](https://postgresql.org)
[![mypy](https://img.shields.io/badge/mypy-strict-blue)](https://mypy-lang.org)
[![tests](https://img.shields.io/badge/tests-681%20(614%20offline)-brightgreen)](#12-테스트와-품질-게이트)

---

## Highlights

| | 설계 | 해결한 문제 |
|--|------|------|
| **상태를 만들지 않음** | Temporal이 단계·타이머·재시도·승인 대기를 전담 | v1에서 가장 아팠던 "이 지원 건이 지금 어느 단계인가" 추적 불능 문제 |
| **AI는 제출하지 않음** | LLM은 `AutomationRecipe`(데이터)만 생성, 실행은 Playwright | LLM 출력이 곧바로 비가역 행위가 되는 위험을 구조적으로 차단 |
| **근거 없는 문장 차단** | Fact 검색 → LLM 생성 → `ground_check` 게이트 | 이력서 hallucination — 모든 불릿에 실재하는 `fact_id` 근거를 강제 |
| **비가역 지점엔 사람** | Telegram 승인 + nonce 1회성 검증 + `DRY_RUN_ONLY` 기본값 | 자동화가 실수로 실제 지원을 제출하는 사고 |
| **부분 제출 방어** | submit 직전 `UNKNOWN` 선기록 → 재개 시 `verify_submission` 우선 | "제출 도중 크래시 — 제출된 건가?"를 판정 불가로 남기지 않음 |
| **DOM 변경 자동 수선** | `AutomationRepairWorkflow` + `repair-{platform}-{form_hash}` dedupe | 사이트 개편으로 N건이 동시에 깨질 때 LLM 호출 N배·알림 N개 방지 |
| **교체 가능성의 증거** | 20개 Port × 구현 2개 이상 × 동일 contract test | "인터페이스만 있고 실제로는 못 바꾸는" 추상화를 만들지 않기 위함 |
| **계층 규칙의 CI 강제** | `import-linter` 7개 계약을 `make check`가 검사 | 의존 방향이 문서에만 있고 코드는 새는 상황 방지 |
| **LLM 비용 설계** | 로컬 `claude` 구독 CLI 어댑터 + 프롬프트 캐시 + 모델 분리 | 종량제 API 키 없이 운용, 분류 작업은 Haiku로 분리 |

---

## 목차

1. [해결하려는 문제](#1-해결하려는-문제)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [지원 1건의 생애주기](#3-지원-1건의-생애주기)
4. [이력서 생성 파이프라인](#4-이력서-생성-파이프라인)
5. [브라우저 실행 계층 — Recipe는 코드가 아니라 데이터](#5-브라우저-실행-계층--recipe는-코드가-아니라-데이터)
6. [핵심 설계 결정과 트레이드오프](#6-핵심-설계-결정과-트레이드오프)
7. [의도적으로 하지 않은 것](#7-의도적으로-하지-않은-것)
8. [실패 분류와 재시도 정책](#8-실패-분류와-재시도-정책)
9. [Ports & Adapters — 교체 가능성 설계](#9-ports--adapters--교체-가능성-설계)
10. [데이터 모델](#10-데이터-모델)
11. [프로젝트 구조](#11-프로젝트-구조)
12. [테스트와 품질 게이트](#12-테스트와-품질-게이트)
13. [설치 및 실행](#13-설치-및-실행)
14. [안전·정책 제약](#14-안전정책-제약)
15. [구현 현황](#15-구현-현황)

---

## 1. 해결하려는 문제

구직 지원 자동화는 언뜻 "폼 채우기 스크립트"처럼 보이지만, 실제로 만들어 보면 다음 네 가지가 동시에 걸립니다.

| 문제 | 왜 어려운가 | 이 저장소의 해결 방식 |
|------|-------------|------------------------|
| **장시간 대기 상태** | 승인 대기 72시간, 예약 실행, 사람의 수정 요청 — 프로세스가 죽어도 살아남아야 합니다 | Temporal durable workflow. 상태를 애플리케이션이 소유하지 않습니다 |
| **LLM의 비결정성** | 생성된 문장이 곧바로 실제 지원서가 되면 되돌릴 수 없습니다 | LLM은 데이터(Recipe/불릿)만 만들고, 판정·조합·실행은 코드가 합니다 |
| **외부 사이트의 변화** | DOM 한 줄이 바뀌면 대기 중인 모든 지원 건이 동시에 깨집니다 | `form_hash` 기반 수선 워크플로우로 N개의 실패를 1개 작업으로 병합합니다 |
| **비가역성** | 지원서 제출은 취소해도 고용주 알림이 회수되지 않습니다 | 최종 submit은 사람 승인 뒤에만, 기본값은 `DRY_RUN_ONLY=true` |

v1에서 가장 큰 문제는 성능이나 정확도가 아니라 **"이 지원 건이 지금 어느 단계인지 아무도 모른다"**는 점이었습니다.
DB에 `status` 컬럼을 두고 코드 곳곳에서 갱신하다 보니, 프로세스가 죽은 지점과 DB가 기억하는 지점이 어긋났고
재개 로직이 매번 특수 케이스가 되었습니다. v2의 설계 목표는 기능 추가가 아니라 **워크플로우를 1급 객체로 만들고
그 상태를 우리가 직접 관리하지 않는 것**입니다.

---

## 2. 시스템 아키텍처

### 2-1. 전체 구성

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  Human Control Plane                                                          │
│                                                                               │
│   Telegram Bot   [승인] [거절] [수정요청] · 자유 텍스트 채팅 · ForceReply 답장  │
└──────────┬────────────────────────────────────────────────────▲───────────────┘
           │ callback_data / command / reply                    │ notify (알림·PDF 첨부)
           │                                                    │
┌──────────▼────────────────────────────────────────────────────┴───────────────┐
│  Control Plane                                                                │
│                                                                               │
│   FastAPI (:8000)                          telegram/listener.py               │
│   ├ POST /applications/{id}/approve        (공인 URL 없는 로컬 개발용 롱폴링)  │
│   ├ POST /applications/{id}/reject                                            │
│   ├ POST /applications/{id}/revise         watchdog.py                        │
│   ├ POST /telegram/webhook                 (visibility API 폴링 → 실패 알림)   │
│   └ GET·POST /recipes/{platform}                                              │
└──────────┬────────────────────────────────────────────────────────────────────┘
           │ start_workflow / signal / query          ※ 라우터는 DB를 직접 쓰지 않습니다
           │
┌──────────▼────────────────────────────────────────────────────────────────────┐
│  Orchestration — Temporal Server (:7233)          실행 상태의 유일한 원본      │
│                                                                               │
│   ApplicationWorkflow  (parent)   application-{application_id}                 │
│    ├── ResumeWorkflow  (child)    resume-{application_id}-{attempt}            │
│    └── AutomationRepairWorkflow   repair-{platform}-{form_hash}  ← dedupe key  │
│                                                                               │
│   JobCollectionWorkflow           Temporal Schedule (cron, 기본 매일 09:00)    │
└───┬──────────────────────┬───────────────────────────┬────────────────────────┘
    │ default queue        │ ai queue                  │ browser queue
    │ (가벼움, 동시성 높게) │ (LLM RPM에 맞춤)           │ (플랫폼당 1~2로 제한)
┌───▼──────────────┐  ┌────▼──────────────────┐  ┌─────▼─────────────────────────┐
│ Worker: default  │  │ Worker: ai            │  │ Worker: browser               │
│  persist_state   │  │  generate_resume      │  │  execute_application          │
│  notify          │  │  review_resume        │  │  verify_submission            │
│  record_attempt  │  │  propose_recipe_diff  │  │  snapshot_dom                 │
│  collect_jobs    │  │  patch_guide          │  │  (heartbeat + 체크포인트 대기) │
└───┬──────────────┘  └────┬──────────────────┘  └─────┬─────────────────────────┘
    │                      │                            │
    │                      ▼                            ▼
    │              ┌───────────────┐            ┌──────────────────┐
    │              │ LLM           │            │ Playwright /     │
    │              │ claude CLI    │            │ agent-browser    │
    │              │ · Anthropic   │            │        │         │
    │              │ · stub        │            │        ▼         │
    │              └───────────────┘            │  wanted/saramin  │
    │                                           │  /jasoseol 등     │
    ▼                                           └──────────────────┘
┌──────────────────────────┐   ┌───────────────────────────────────────┐
│ PostgreSQL               │   │ Blob Storage (local FS / S3 · MinIO)   │
│ 비즈니스 데이터(projection)│   │ 이력서 PDF · DOM 스냅샷 · 실행 스크린샷 │
└──────────────────────────┘   └───────────────────────────────────────┘
```

### 2-2. Task Queue를 3개로 나눈 이유

| Queue | Worker 특성 | 동시성 | 한 큐에 몰면 |
|-------|-------------|--------|--------------|
| `default` | 가벼움, DB 트랜잭션 위주 | 높게 (수십) | — |
| `ai` | 느림, 토큰 비용, rate limit | 중간 (LLM RPM에 맞춤) | 승인 처리 같은 가벼운 작업까지 LLM 대기에 막힙니다 |
| `browser` | 세션당 메모리 수백 MB, 플랫폼 rate limit | **낮게 (플랫폼당 1~2)** | Playwright 세션이 워커를 잡아먹습니다 |

`browser` 큐의 동시성 제한은 성능 튜닝이 아니라 **안전장치**입니다.
"같은 사이트에 동시에 3건 지원" 같은 사고를 애플리케이션 로직이 아니라 인프라 구조로 막습니다.

### 2-3. 계층 구조와 의존 방향

의존 방향은 문서가 아니라 `import-linter`가 CI에서 강제합니다(`make arch`).

```
   ┌──────────────────────────────────────────────────────────────────┐
   │  api          라우터는 컨테이너에서 어댑터를 꺼내 씁니다           │
   ├──────────────────────────────────────────────────────────────────┤
   │  bootstrap ★  어댑터를 생성하는 유일한 파일 (composition root)    │
   ├──────────────────────────────────────────────────────────────────┤
   │  workflows    contracts / domain 만 import — 결정적이어야 합니다   │
   ├──────────────────────────────────────────────────────────────────┤
   │  activities   port를 주입받는 activity 구현 — 모든 I/O가 여기에    │
   ├──────────────────────────────────────────────────────────────────┤
   │  adapters     port 구현체. 상위 계층을 모릅니다                    │
   ├──────────────────────────────────────────────────────────────────┤
   │  ports        Protocol 정의. 구현을 모릅니다                       │
   ├──────────────────────────────────────────────────────────────────┤
   │  contracts    workflow-safe DTO + activity stub. 벤더 SDK 금지     │
   ├──────────────────────────────────────────────────────────────────┤
   │  domain       순수. 프레임워크·어댑터 무의존                       │
   └──────────────────────────────────────────────────────────────────┘

   금지되는 화살표의 예 (모두 CI에서 실패합니다)
     domain      ──✗──► sqlalchemy / temporalio / playwright / anthropic
     workflows   ──✗──► adapters / activities / boto3
     ports       ──✗──► adapters (구현을 아는 순간 추상화가 아닙니다)
     api         ──✗──► adapters (직접 import 금지, bootstrap을 거쳐야 합니다)
```

`contracts`가 별도 계층인 이유는 Temporal 때문입니다. 워크플로우 payload로 오가는 타입은
샌드박스 안에서 import되므로 벤더 SDK가 섞이면 결정성이 깨집니다. 그래서 DTO·Recipe·activity stub은
`pydantic`/stdlib만 쓰는 별도 계층으로 분리했습니다.

---

## 3. 지원 1건의 생애주기

`ApplicationWorkflow` 하나가 지원 1건의 전 생애를 소유합니다. 아래 상태는 DB 컬럼이 아니라
워크플로우의 실행 지점이며, DB의 `applications.status`는 그것의 **projection**입니다.

```
                     collect_job
   [start] ──────────────────────────► collecting
                                          │ evaluate (하드컷·트랙·스코어링)
                                          ▼
                        ┌────────── evaluating ──────────┐
                        │ 자격 미달 / 중복               │ 통과
                        ▼                                ▼
                     rejected                    generating_resume ◄──────────┐
                                                         │                    │
                                          child ResumeWorkflow                │
                                                         ▼                    │
                                                     reviewing                │
                                                  │           │ FAIL          │
                                             PASS │           └───────────────┘
                                                  ▼             (최대 3회)
                                             rendering_pdf
                                                  │  WeasyPrint → PDF → Blob
                                                  ▼
                    ┌──────────────── awaiting_approval ────────────────┐
                    │  Telegram [승인] [거절] [수정요청]                 │
                    │  · nonce 1회성 검증 (오래된 버튼 재사용 차단)      │
                    │  · 72시간 timeout                                 │
                    └───┬─────────┬──────────┬───────────┬──────────────┘
              revise    │  approve│    reject│    timeout│
        (MAX_REVISIONS) │         │          │           │
          ┌─────────────┘         ▼          ▼           ▼
          │                   scheduled   rejected    expired
          │                       │  ▲ reschedule signal
   generating_resume 로 복귀      │  │
   · SPECIFIC: 이번 1회만 반영    │  └── cancel signal ──► cancelled
   · GENERAL : 가이드 파일에 영속 │
     (사람이 diff를 2차 승인)     ▼  durable timer 만료
                              executing ─────────────────► verifying ──► completed
                                  │  │                          │
                                  │  │ RecipeExecutionError     │ 검증 실패
                                  │  ▼                          ▼
                                  │ repairing ──► (수선 성공) ─► executing (1회 재시도)
                                  │      │
                                  │      └── 수선 실패 ──┐
                                  └── NonRetryable ──────┴──► needs_human
```

**설계상 눈여겨볼 지점**

- `awaiting_approval`은 `workflow.sleep`이 아니라 `workflow.wait_condition(..., timeout=)`으로 대기합니다.
  `sleep`으로 막으면 대기 중에 도착한 reschedule/cancel signal을 받지 못합니다.
- 승인 대기는 while 루프입니다. 수정 요청(REVISE)이 반복될 수 있고, 매 회차마다 새 nonce가 발급됩니다.
- `executing → verifying`은 성공 경로에서만 도는 것이 아닙니다. 실행 activity가 **실패해도**
  먼저 `verify_submission`을 돌려 실제로 제출됐는지 확인한 뒤에야 `needs_human`으로 넘깁니다([§6-5](#6-5-부분-제출은-확인-불가능으로-남기지-않습니다)).

---

## 4. 이력서 생성 파이프라인

LLM에게 "이력서를 써 줘"라고 시키면 없는 경력을 만들어냅니다. 그래서 **사실은 사람이 소유하고,
구조는 코드가 조립하고, LLM은 문장만 씁니다.**

```
config/facts.yaml            config/profile.yaml         config/resume_guide.{platform}.md
 (검증된 사실 = 유일한 원천)   (이름·연락처·학력·스킬)      (사람이 누적한 서술 규칙)
        │                            │                            │
        ▼                            │                            │
  retrieve_facts                     │                            │
        │                            │                            │
        ▼                            │                            │
  select_relevant_facts              │                            │
  키워드 겹침 랭킹 (순수 domain)      │                            │
        │                            │                            │
        ▼                            │                            │
  group_facts_for_resume             │                            │
  회사/기간/블록 구조를 코드가 결정론적으로 조립                     │
        │                            │                            │
        ▼                            │                            │
  ┌──────────────────────────────────┴────────────────────────────┴──────────┐
  │  LLM 호출 — 블록 안의 "불릿 문장"만 생성합니다                             │
  │  구조화 출력 강제: tool-use(Anthropic) / --json-schema(claude CLI)        │
  └──────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
                     ┌──────── ground_check ────────┐
                     │ 모든 하이라이트/불릿에         │
                     │ ① 근거 fact_id가 있는가?      │
                     │ ② 그 id가 실재하는가?         │
                     └───┬──────────────────┬────────┘
                    실패 │                  │ 통과
                         ▼                  ▼
              최대 2회 재프롬프트      AssembledResume
                         │             (블록 메타 + 불릿 + 프로필)
                  그래도 실패           │
                         ▼             ▼
              LLMSchemaViolation   WeasyPrint → PDF → Blob → Telegram 첨부
              (NON_RETRYABLE —
               같은 실패의 반복을 끊습니다)
```

**트레이드오프 노트**

- **왜 블록 구조를 LLM에게 맡기지 않는가** — 회사명·기간·블록 제목은 창의성이 필요 없는 정형 정보인데,
  LLM에게 맡기면 기간이 미묘하게 틀리거나 회사가 합쳐집니다. `domain/resume_blocks.py`가 결정론적으로 조립하고
  LLM은 그 안을 채웁니다. Recipe와 같은 "AI는 생성만, 판정·조합은 코드" 원칙의 연장입니다.
- **왜 스키마 위반을 non-retryable로 끊는가** — Temporal의 activity 재시도는 *일시적* 실패를 위한 것입니다.
  같은 프롬프트로 같은 모델을 다시 부르면 같은 실패가 반복될 뿐이므로, activity 내부에서 2회 재프롬프트한 뒤
  실패하면 재시도 대상에서 제외합니다.
- **왜 2차 LLM 리뷰어를 두지 않는가** — [§7](#7-의도적으로-하지-않은-것)에서 다룹니다.

---

## 5. 브라우저 실행 계층 — Recipe는 코드가 아니라 데이터

```
PlatformAdapter          AutomationRecipe            RecipeExecutor
(공고 조회·제출 검증)  →  (실행 계획 = 순수 데이터)  →  (Playwright / agent-browser)
                                  │
                                  ├─ Pydantic 스키마 검증  (extra="forbid" — LLM의 창작 필드 차단)
                                  └─ 정책 검증 (domain/recipe_policy.py)
                                       · value_literal 에 자격증명/토큰 패턴 금지
                                       · SUBMIT 은 마지막에 최대 1회
                                       · goto 도메인은 플랫폼 allowlist 안에서만
                                       · 액션 수·타임아웃 상한
```

Recipe를 데이터로 둔 덕분에 얻은 것들입니다.

| 얻은 것 | 설명 |
|---------|------|
| **버전 관리** | `draft → candidate → active → deprecated` 상태 전이. 승격은 사람이 합니다 |
| **롤백** | 실패한 버전을 되돌리는 것이 코드 배포가 아니라 레코드 상태 변경입니다 |
| **LLM 수선 가능** | 수선이 "코드 생성"이 아니라 "JSON diff 제안"이 되어 검증 가능해집니다 |
| **정책 게이트** | 실행 계층에 닿기 전 코드가 판정합니다. 프롬프트 신뢰에 의존하지 않습니다 |

**실행 엔진이 두 개인 이유** — `PlaywrightExecutor`와 `AgentBrowserExecutor`는 대체 관계가 아니라 동일 계약의 두 대역입니다.
디버깅에 쓰는 agent-browser CLI와 프로덕션 Playwright가 같은 selector를 다르게 해석하는 사례를 실측했기 때문에
(예: 접근성 이름 매칭이 한쪽에서만 동작), 두 엔진을 같은 contract test로 묶어 번역 계층 버그를 드러내도록 했습니다.

**SUPERVISED 체크포인트** — `ExecutionMode.SUPERVISED`는 페이지 경계마다 스크린샷을 찍어 사람에게 보내고,
브라우저를 띄운 채 `activity.heartbeat()`로 생존신호를 보내며 승인을 대기합니다.
`SUBMIT` 액션은 recipe에 플래그가 없어도 **항상** 강제 체크포인트입니다.

---

## 6. 핵심 설계 결정과 트레이드오프

이 섹션이 이 저장소에서 가장 읽을 가치가 있는 부분이라고 생각합니다.
각 항목은 "무엇을 골랐는가"보다 **"무엇을 포기했는가"**를 먼저 적었습니다.

### 6-1. 진행 상태를 DB에 두지 않습니다

| | |
|---|---|
| **선택** | Temporal = 실행 상태의 원본, DB = 비즈니스 데이터. `applications.status`는 projection |
| **대안** | DB에 `status` 컬럼을 두고 각 단계에서 UPDATE (v1의 방식) |
| **포기한 것** | `SELECT * FROM applications WHERE status='executing'` 같은 즉석 조회의 편의성 |
| **얻은 것** | 프로세스가 죽은 지점과 기록된 지점이 어긋날 수 없습니다. 재개 로직이 아예 필요 없습니다 |

이 원칙을 지키기 위해 **status를 쓰는 통로를 `persist_state` activity 하나로 제한**했습니다.
activity는 최소 1회 실행이 보장될 뿐 정확히 1회가 아니므로, 이 activity는
`(application_id, workflow_run_id, state)` 기준으로 멱등 upsert합니다.

> "지금 어디까지 갔지?"는 Temporal query, "지난달 몇 건 지원했지?"는 DB.
> `applications.workflow_id`가 두 세계를 잇는 유일한 조인 키이며, 모든 로그·스토리지 키에 이 값이 들어갑니다.

### 6-2. workflow_id를 멱등성 키로 씁니다

`application-{application_id}`로 고정하면 API가 실수로 두 번 호출돼도
`WorkflowIDReusePolicy.REJECT_DUPLICATE`가 중복 지원을 막습니다. **애플리케이션 레벨 중복 방지 로직이 필요 없습니다.**

더 중요한 것은 수선 워크플로우입니다.

```
DOM 변경 발생
      │
      ├── 지원 건 A 실패 ─┐
      ├── 지원 건 B 실패 ─┤   workflow_id = repair-wanted-{form_hash}
      ├── 지원 건 C 실패 ─┼──────────────────► 수선 워크플로우 1개로 병합
      └── 지원 건 D 실패 ─┘                     (LLM 호출 1회, 사람 알림 1개)
```

**미해결로 남겼던 지점과 그 결론** — "이미 도는 수선 작업에 뒤늦게 붙어서 기다리기"는 채택하지 않았습니다.
Temporal 워크플로우 코드 안에서 *자신의 child가 아닌* 임의 워크플로우의 완료를 기다릴 표준 API가 없기 때문입니다.
대신 `WorkflowAlreadyStartedError`를 잡아 **그 지원 건만 포기**시킵니다.
수선이 끝나면 다음 지원 건이 새 recipe로 정상 실행되므로, 복잡한 대기 메커니즘을 만들 만큼의 이득이 없다고 판단했습니다.

### 6-3. nonce를 어댑터가 아니라 워크플로우가 소유합니다

Telegram 버튼은 두 번 눌립니다. 오래된 메시지의 버튼도 눌립니다. 그래서 1회성 nonce가 필요한데,
**어디에 저장할 것인가**가 실제 문제였습니다.

```
   ✗ 처음 시도: Notifier 어댑터의 프로세스 메모리에 저장

        worker 프로세스              webhook 서버 / 리스너 프로세스
        ┌──────────────┐            ┌──────────────────────────┐
        │ nonce 발급   │            │ nonce 검증               │
        │ {abc: ...}   │   ✗ 공유 불가 │ {} ← 비어 있습니다        │
        └──────────────┘            └──────────────────────────┘
                       → 라이브 스모크 테스트에서 항상 실패

   ✓ 현재: ApplicationWorkflow가 직접 소유 (_decision_nonce / _nonce_ok)

        worker 프로세스                webhook 서버 / 리스너
        ┌──────────────┐              ┌──────────────────────┐
        │ Workflow     │◄── signal ───┤ 콜백에서 nonce 전달   │
        │ nonce 보관   │              └──────────────────────┘
        └──────────────┘   Temporal이 프로세스 경계를 넘겨줍니다
```

이 "프로세스 경계에 상태를 두지 않는다"는 교훈은 이후 두 곳에 더 적용됐습니다.

- **REVISE 자유 텍스트** — 사용자의 ForceReply 답장이 어느 지원 건/nonce/scope에 속하는지를,
  프롬프트 본문에 `[revise:{application_id}:{nonce}:{scope}]` 태그를 실어 보내고
  답장이 담아오는 `reply_to_message.text`에서 파싱해 복원합니다. **상태 없이 왕복합니다.**
- **SUPERVISED 체크포인트** — 여기서 기다리는 주체는 워크플로우가 아니라 activity 자신이라 signal을 쓸 수 없어,
  별도 `CheckpointStore` port(파일/메모리 구현)를 두고 승인 프로세스가 그곳에 결정을 기록합니다.

### 6-4. 가이드는 LLM이 재작성하지 않고, 코드가 치환합니다

"자기소개를 더 짧게" 같은 피드백을 영구 규칙으로 반영할 때(REVISE·GENERAL),
LLM에게 가이드 전문을 다시 쓰게 하면 **지시하지 않은 기존 규칙이 조용히 사라집니다.**

```
   ✗ 전문 재작성                        ✓ 치환 쌍 + 코드 적용
   ┌──────────────────┐                ┌──────────────────────────────┐
   │ LLM: 가이드 전체  │                │ LLM: [{old: "...", new:"..."}]│
   │ 를 다시 써 주세요 │                └────────────┬─────────────────┘
   └────────┬─────────┘                             │
            ▼                          domain/guide_patch.apply_patch
   규칙 3개 중 1개가                    · old가 정확히 1번 매치될 때만 적용
   흔적 없이 삭제됨                     · 0번/2번 이상이면 거부
                                                    │
                                        사람이 diff를 2차 승인 (별도 nonce)
                                                    ▼
                                        config/resume_guide.{platform}.md
```

가이드는 **이후 모든 생성에 영향을 주는 레버**이므로 되돌리기 어려운 축에 속합니다.
그래서 본 승인과는 **별도의 nonce/decision 슬롯**을 씁니다 — 섞으면 "가이드 반영 승인" 클릭이
"지원 승인"으로 해석될 수 있기 때문입니다.

### 6-5. 부분 제출은 "확인 불가능"으로 남기지 않습니다

submit 도중 크래시하면 제출 여부를 알 수 없습니다. 이때 **잘못된 확인이 놓친 제출보다 위험**합니다.

```
   submit 직전                    실행                  재개 / 실패 처리
   ┌───────────────┐        ┌────────────┐        ┌──────────────────────────┐
   │ attempts 테이블│        │  crash 💥  │        │ ① verify_submission 먼저 │
   │ UNKNOWN 선기록 │───────►│            │───────►│ ② 결과로 attempt 덮어쓰기 │
   │ (submitting)  │        └────────────┘        │ ③ 그래도 불확실 → 사람    │
   └───────────────┘                              └──────────────────────────┘
        (application_id, attempt) 기준 멱등 upsert
```

`verify_submission`은 "내 지원 현황" API를 `job_id`로 조회하되 **`since`(이번 시도의 시작 시각)로도 대조**합니다.
`job_id`만 보면 "예전에 같은 공고에 지원한 적 있음"이 이번 시도의 성공으로 오탐되기 때문입니다.
서버 시각과 워크플로우 시각(UTC) 사이 오차는 5분 여유로 흡수합니다.

또한 `verify_submission` **호출 자체가 실패해도**(세션 만료 등) 이를 잡아 "확인 안 됨"으로 안전하게 떨어뜨립니다.
잡지 않으면 워크플로우가 조용히 FAILED로 죽습니다.

### 6-6. 실패의 가시성은 워크플로우 안에서만 확보되지 않습니다

`try/except`로 감싸는 것은 **알고 있는 실패 지점**만 덮습니다.
코드 버그, 사람의 실수로 인한 terminate, `workflow_execution_timeout`처럼
워크플로우 코드가 아예 더 돌지 못하는 종료는 그 안에서 잡을 수 없습니다.

그래서 프로세스 **밖**에 백스톱을 두었습니다.

```
   watchdog.py ──► Temporal visibility API(list_workflows) 주기 폴링
                     └─► FAILED / TERMINATED / TIMED_OUT ──► Notifier ──► Telegram
```

재시작 사이의 워터마크를 영속화하지 않고 `WATCHDOG_LOOKBACK_MINUTES`만큼 다시 훑습니다.
**놓치는 것보다 중복 알림이 싼 트레이드오프**이며, 텔레그램 리스너와 같은 선택입니다.

### 6-7. LLM 비용 — API 키 종량제 대신 로컬 구독을 씁니다

`ClaudeCodeCliLLM`은 `LLMClient`의 세 번째 구현으로, 이 머신에 로그인된 Claude Code 구독으로
`claude` CLI를 headless subprocess로 호출합니다.

**실측으로 확정한 두 가지**

1. **`--bare`는 쓰지 않습니다.** OAuth/keychain을 읽지 않고 API 키 인증을 강제해,
   정확히 피하려던 과금 방식으로 되돌아갑니다. 대신 하네스를 낱개로 걷어냅니다 —
   `--tools ""`, `--strict-mcp-config`, `--disable-slash-commands`, `--setting-sources ""`, `--system-prompt` 교체.
   `--model`도 항상 명시해 CLI의 모델 라우팅용 분류기 호출까지 없앱니다.
2. **새 프로세스 1회성 호출에는 프롬프트 캐시가 전혀 붙지 않습니다.**
   그래서 `LLMClient`에 선택 파라미터 `cache_key`를 추가하고, 같은 사용자의 Fact/Profile 프리픽스를
   여러 공고 생성에 걸쳐 재사용합니다. `AnthropicLLM`도 같은 파라미터로 `cache_control: ephemeral`을 붙입니다.

**모델도 용도별로 분리했습니다.** 채팅 에이전트의 "도구를 부를지 답할지" 판단은 이력서 생성보다 훨씬 가벼운 분류 작업이므로,
`c.llm`(이력서 생성, Sonnet)과 `c.chat_llm`(도구 선택, 기본 Haiku)을 같은 프로바이더의 별개 인스턴스로 나눴습니다.

**장애도 등급을 나눕니다.** 재시도로 풀리지 않는 두 실패(로그인 풀림 · 구독 한도 초과)는
실측 시그니처로 구분해 `LLMAuthRequired`/`LLMQuotaExceeded`로 던지고, 워크플로우가 이를 알아보면
재던지기 전에 사람에게 알립니다. 분류되지 않은 실패(타임아웃 등)는 재시도 대상이므로 알리지 않습니다.

### 6-8. 채팅 에이전트의 ReAct 루프를 직접 짰습니다

`LLMClient` port에는 `complete`/`structured`만 있고 멀티턴 tool-use가 없습니다.
port를 넓히는 대신, 그 위에서 루프를 직접 구성했습니다.

```
   사용자 자유 텍스트
        │
        ▼
   ┌──────────────────────────────────────────────────┐
   │ structured(AgentStep)  ← discriminator 필드 하나  │
   │   action: "call_tool" | "respond"                 │
   └───────┬──────────────────────────┬────────────────┘
           │ call_tool                │ respond
           ▼                          ▼
   TOOLS 레지스트리(코드)가 실행    사용자에게 답장
           │
           └──► 결과를 다음 턴 프롬프트에 첨부 ──► 반복
```

- LLM은 **"무엇을 할지"만 고르고**, 실제 실행은 코드가 합니다.
- 행동성 도구도 허용하되, **실제 mutate는 여전히 사람이 기존 버튼을 눌러야 일어납니다.**
  예컨대 `resend_pending_decision`은 워크플로우 query로 nonce를 읽어와 원래 버튼을 다시 보낼 뿐,
  새 signal을 쏘지 않습니다. 자연어 오인식이 승인 절차를 우회하지 못하게 하기 위함입니다.
- `start_applications`는 실제로 워크플로우를 시작하지만, **최종 제출은 그 안에서도 사람 승인 뒤에만** 일어납니다.
  비가역성의 진짜 불변식은 "워크플로우를 안 건드린다"가 아니라 "제출은 못 건드린다"입니다.
- 도구 하나가 실패해도, LLM 호출 자체가 실패해도 예외를 던지지 않고 사과 메시지로 마무리합니다
  (webhook 라우트가 500을 내지 않아야 하기 때문입니다).

### 6-9. 도메인 예외는 Temporal을 건너면 문자열이 됩니다

`isinstance`로 분기하다 조용히 실패하는 함정이라 명시해 둡니다.

```python
# ✗ 워크플로우 안에서는 동작하지 않습니다 — 원래 클래스가 남아 있지 않습니다
except LLMQuotaExceeded:
    ...

# ✓ ApplicationError 로 감싸지고, 원래 타입은 .type 문자열로만 남습니다
except ActivityError as e:
    if getattr(e.cause, "type", None) == "LLMQuotaExceeded":
        ...
```

같은 맥락에서, **워커 재시작을 테스트할 때는 `Worker(..., max_cached_workflows=0)`**를 씁니다.
sticky execution이 켜져 있으면 서버가 죽은 워커의 sticky 큐로 계속 라우팅해 테스트가 에러도 없이 멈춥니다.

---

## 7. 의도적으로 하지 않은 것

"무엇을 만들지 않았는가"도 설계입니다. 아래는 검토 후 **보류를 문서에 남긴** 항목들입니다.

| 하지 않은 것 | 이유 |
|--------------|------|
| **오케스트레이션 프레임워크(LangGraph 등) 도입** | 이력서 파이프라인이 여전히 **분기·병렬이 없는 선형 체인**이라 도입 기준을 채우지 못합니다. 다만 특정 프레임워크로 못박지도 않았습니다 — `ai/`는 순수 Pydantic + 문자열 함수로만 두어, 나중에 어느 쪽으로든 같은 port 뒤에서 갈아끼울 수 있게 열어두었습니다 |
| **2차 LLM 이력서 리뷰어** | hallucination은 `ground_check`가 이미 코드로 막고 있고, 품질 판단은 Telegram 승인과 REVISE가 커버합니다. LLM으로 LLM을 검사하는 것은 비용은 확실하고 효과는 불확실합니다 |
| **DB 상태 캐시 / 이중 기록** | [§6-1](#6-1-진행-상태를-db에-두지-않습니다)의 불변식을 깨는 순간 v1의 디버깅 지옥이 재현됩니다 |
| **AI Recipe의 자동 승격** | `draft → candidate → active`는 사람이 승격합니다. 실행이 한 번 성공했다는 사실은 그 recipe가 안전하다는 증거가 아닙니다 |
| **credential vault 연동** | 검토했으나 `.env` 평문으로 시작했습니다. 나중에 바꿀 수 있는 결정으로 남겨둔, 의도적인 부채입니다 |
| **범용 웹 에이전트 실행기(외부 ATS 대응)** | 구현·테스트까지 마쳤으나 현재는 **최후순위로 동결**했습니다. Recipe 기반 경로가 주력인 상태에서 유지 비용이 이득을 넘어섭니다 |
| **CAPTCHA·추가 인증 우회** | 정책상 절대 하지 않습니다. [§14](#14-안전정책-제약)를 참고해 주세요 |

---

## 8. 실패 분류와 재시도 정책

"일단 3번 재시도"는 v1에서 문제를 숨긴 방식이었습니다. v2는 **에러 타입이 재시도 정책을 결정**합니다.

| 에러 | 성격 | 처리 |
|------|------|------|
| 네트워크 / 타임아웃 / 5xx | 일시적 | 재시도 (exponential backoff, 최대 5회) |
| LLM rate limit | 일시적 | 재시도 + backoff 크게 |
| LLM 출력 스키마 위반 | 준일시적 | activity 내부에서 2회 재프롬프트 → 실패 시 non-retryable |
| `RecipeExecutionError` | 구조 변경 | **재시도 금지** → `AutomationRepairWorkflow` |
| `CaptchaEncountered` | 정책 | 재시도 금지 → 사람 |
| `AuthRequired` | 세션 만료 | 재시도 금지 → 사람에게 재로그인 요청 |
| `LLMAuthRequired` / `LLMQuotaExceeded` | 구독/인증 | 재시도 금지 + **Telegram 알림** |
| `CheckpointDeclined` | 사람의 거절·타임아웃 | 재시도 금지 |
| `AlreadySubmitted` | 멱등 충돌 | 성공으로 간주 (verify로 확인) |
| 자격 미달 | 정상 종료 | 재시도가 아니라 `rejected` |

재시도 금지 대상은 `domain/errors.py`의 `NON_RETRYABLE`에 등록되어 RetryPolicy로 전달됩니다.
목록을 한곳에 모아 둔 이유는, 새 예외를 추가할 때 "이건 재시도해도 되는가"를 반드시 한 번 생각하게 만들기 위해서입니다.

---

## 9. Ports & Adapters — 교체 가능성 설계

추상화의 목적은 "언젠가 바꿀 수 있음"이 아니라 **"지금 실제로 바꿔 끼워 테스트가 통과함"**입니다.
그래서 모든 port는 **구현이 2개 이상**이고, **하나의 contract test 스위트가 모든 구현에 대해 돕니다.**

| Port | 구현 | 비고 |
|------|------|------|
| `LLMClient` | `stub` · `anthropic` · `claude_cli` | 구조화 출력 계약(`LLMSchemaViolation`)을 셋 다 지킵니다 |
| `RecipeExecutor` | `replay` · `playwright` · `agent_browser` | `replay`는 고정 HTML fixture 대상 — 인프라 없이 테스트 가능합니다 |
| `UnitOfWork` / `*Repository` | `memory` · `file` · `postgres` | postgres는 DTO를 JSONB `payload`에 담고 조회 키만 컬럼으로 승격했습니다 |
| `BlobStore` | `memory` · `local` · `s3` | S3(MinIO)는 virtual-hosted-style DNS를 못 풀어 `addressing_style="path"` 고정 |
| `Notifier` | `console` · `telegram` | port에는 "Telegram"이라는 단어가 없습니다 |
| `PdfRenderer` | `stub` · `weasyprint` | macOS에서 필요한 `DYLD_FALLBACK_LIBRARY_PATH` 보정을 어댑터 로드 시점에 수행합니다 |
| `JobSource` | `fixture` · `wanted` · `saramin` · `jasoseol` | |
| `RecipeSource` | `memory` · `jsonfile` | `versions()`/`promote()`가 승격 invariant를 강제합니다 |
| `FactSource` · `ProfileSource` · `GuideSource` · `PortfolioSource` · `MatchingConfigSource` | 각각 `static` · 파일 기반 | 캐시 없이 매번 새로 읽습니다 — 편집 즉시 반영이 운영상 더 중요합니다 |
| `PlatformAdapter` | `fixture` · `wanted` · `saramin` | 공고 단건 조회 + 제출 검증 |
| `AttachmentManager` | `fixture` · `wanted` | 지원할 때마다 쌓이는 고아 이력서 파일 정리 |
| `CheckpointStore` | `memory` · `file` | SUPERVISED 체크포인트 결정 전달 |
| `WebAgentExecutor` | `replay` · `aside_cli` | 현재 동결 상태입니다([§7](#7-의도적으로-하지-않은-것)) |
| `CredentialSource` · `Clock` · `IdGen` | `static` · 실제 구현 | 테스트 결정성 확보용 |

### 새 외부 의존성을 추가하는 절차

순서를 지키는 것 자체가 설계 장치입니다.

```
1. ports/ 에 Protocol 정의        벤더 타입을 시그니처에 노출하지 않습니다
2. 예외 계약을 정합니다            port가 새는 곳은 반환값이 아니라 예외 쪽입니다
3. 구현 2개 작성                  실제 어댑터 + 오프라인/테스트 대역
4. contract test params 에 추가    하나의 스위트가 모든 구현에 대해 돌아야 합니다
5. bootstrap 과 config 에 배선     선택지가 존재하는 유일한 파일
```

2번(예외 계약)을 명시적 단계로 둔 이유는, 추상화가 깨지는 지점이 대부분 반환 타입이 아니라
**구현체별로 다른 예외가 그대로 올라오는 것**이었기 때문입니다.

---

## 10. 데이터 모델

```
   users ──┬──< facts                 이력서 생성의 유일한 사실 원천
           ├──< resumes ──┐
           └──< applications ──┬──< application_attempts   실행 1회 = 1행 (감사 로그)
                     ▲         └──< approvals              nonce 로 버튼 재사용 차단
   jobs ─────────────┘
   platforms ──< automation_recipes ──< application_attempts
             └──< platform_policies    rate limit 근거
```

| Table | 핵심 컬럼 | 비고 |
|-------|-----------|------|
| `facts` | user_id, kind, content, source, entity/block 그룹핑 키 | **이력서 생성의 유일한 사실 원천** |
| `jobs` | platform, external_id, url, title, company, raw | `UNIQUE(platform, external_id)` |
| `applications` | user_id, job_id, resume_id, status, workflow_id, scheduled_at, result | `UNIQUE(user_id, job_id)` = 중복 지원 방지 |
| `application_attempts` | application_id, recipe_version, mode, outcome, error_code, artifact_keys | `(application_id, attempt)` 기준 멱등 upsert |
| `approvals` | application_id, channel, decision, nonce, decided_at | |
| `automation_recipes` | platform, version, status, form_hash, spec | `UNIQUE(platform, version)` |

**Blob 레이아웃**

```
resumes/{user_id}/{resume_id}.pdf
portfolios/{user_id}/{file_id}
dom-snapshots/{platform}/{form_hash}/{ts}.html.gz
application-artifacts/{application_id}/{attempt}/{step}.png
checkpoints/{application_id}/{attempt}/{id}.png
```

---

## 11. 프로젝트 구조

```
auto-apply-v2/
├── docker-compose.yml          postgres · temporal · temporal-ui · minio
├── alembic/                    마이그레이션 (autogenerate + upgrade/downgrade 왕복 검증 완료)
├── config/
│   ├── facts.yaml              검증된 사실 — 개인정보라 gitignore, *.example.yaml 이 형식 공유
│   ├── profile.yaml            이름·연락처·학력·스킬 태그 (LLM 을 거치지 않는 정형 정보)
│   ├── matching.yaml           하드컷 · 트랙 · 스코어링 규칙
│   └── resume_guide.*.md       REVISE(GENERAL) 로 누적되는 서술 규칙
│
├── src/auto_apply/
│   ├── domain/          ★ 순수 — 프레임워크 무의존, 테스트가 가장 쉬운 곳
│   │   ├── job_screening.py · job_applicability.py    적합도 · 지원가능성 판정
│   │   ├── resume_matching.py · resume_blocks.py      Fact 랭킹 · 블록 조립 · ground_check
│   │   ├── recipe_policy.py · recipe_selector.py      정책 검증 · 동적 selector 치환
│   │   ├── guide_patch.py                             치환 쌍 적용 (1회 매치 시에만)
│   │   ├── login_flow.py                              CAPTCHA·추가인증 감지 = 안전장치 본체
│   │   ├── chat_agent.py                              도구 카탈로그 → 프롬프트 조립
│   │   └── errors.py                                  NON_RETRYABLE 목록
│   │
│   ├── contracts/       ★ workflow-safe DTO + activity stub (벤더 SDK 금지)
│   ├── ports/           ★ Protocol 정의 20개 모듈. 구현을 import 하지 않습니다
│   ├── adapters/        ★ port 별 구현체. 서로를 모릅니다
│   ├── ai/              ★ 프레임워크 무의존: 순수 Pydantic 스키마 + 프롬프트 조립
│   │
│   ├── workflows/
│   │   ├── application.py    지원 1건의 전 생애 (parent)
│   │   ├── _execution.py     실행 + 검증 + 감사 로그   ┐ application.py 한 파일에
│   │   ├── _revision.py      REVISE 재생성 · 가이드 patch │ 다 넣으면 책임이 흐려져
│   │   ├── _repair.py        수선 child 기동 · dedupe   ┘ 분리했습니다
│   │   ├── resume.py · repair.py · job_collection.py
│   ├── activities/      모든 I/O 가 여기에만 존재합니다
│   ├── telegram/        bridge(콜백 라우팅) · listener(롱폴링) · agent(ReAct) · _agent_tools
│   ├── api/             FastAPI 라우터 — 컨테이너에서 꺼내 씁니다
│   │
│   ├── bootstrap.py     ★ composition root: 어댑터를 생성하는 유일한 파일
│   ├── worker.py        --queue {default|ai|browser}
│   ├── watchdog.py      워크플로우 능동 감시
│   ├── schedule.py      공고 수집 Schedule 등록/삭제
│   ├── apply_intake.py  캐시 기반 지원 후보 선정 (채팅 에이전트 도구가 사용)
│   └── resume_cleanup.py  플랫폼 고아 첨부파일 정리 (기본 dry-run)
│
├── scripts/
│   ├── explore_platform.sh   ★ 라이브 탐색 전용 — 네트워크 route abort 로 오제출 차단
│   ├── auto_login.py         본인 계정 자동 재로그인 (CAPTCHA 시 즉시 중단)
│   └── save_auth_state.py    사람이 수동 로그인해 storage_state 저장
│
└── tests/
    ├── ports/       ★ contract test — 모든 구현체에 동일 스위트
    ├── workflows/   Temporal test env (시간 스킵 → 72시간 대기를 즉시 검증)
    ├── recipes/     고정 HTML fixture 대상 executor 테스트
    ├── schemas/     LLM 출력 스키마 회귀 테스트
    └── domain/      순수 함수 테스트
```

`scripts/explore_platform.sh`는 사고 대응의 산물입니다. 라이브 탐색 중 실제 지원이 잘못 제출된 적이 있었고
(취소했지만 고용주 알림 메일은 회수되지 않습니다), 이후 탐색은 **네트워크 레벨에서 제출 요청을 abort**하는
이 스크립트를 반드시 거치도록 절차를 고정했습니다.

---

## 12. 테스트와 품질 게이트

```bash
make check   # lint + type + arch + test  ← 커밋 전 필수
```

| 게이트 | 도구 | 검사 내용 |
|--------|------|-----------|
| `lint` | ruff | pycodestyle · pyflakes · isort · bugbear · **ANN(타입 애노테이션 강제)** · **TID252(상대 import 금지)** |
| `type` | mypy `strict` | `warn_unreachable` 포함, pydantic 플러그인 |
| `arch` | import-linter | [§2-3](#2-3-계층-구조와-의존-방향)의 계층 계약 7개 |
| `test` | pytest | 전체 **681개**, `make check`가 도는 것(Docker/네이티브 바이너리 불필요) **614개** (그중 17개는 동결된 기능이라 skip — [§7](#7-의도적으로-하지-않은-것)) |

**테스트 전략의 원칙**

- **기본 `make test`는 Docker 없이 항상 돌아야 합니다.** 마커는 "무엇이 있어야 도는가"로만 가릅니다 —
  `@pytest.mark.docker`(postgres/minio 등 `make up` 인프라), `@pytest.mark.native`(playwright 브라우저·
  agent-browser CLI·weasyprint 의 cairo/pango 등 이 머신에 설치된 바이너리), `@pytest.mark.temporal`
  (Temporal 테스트 서버 — Docker 는 아니라서 `make check`엔 포함되고, 느릴 뿐이라 `make test-fast`에서만 뺍니다).
  아무 마커도 없으면 기본 실행 대상입니다.
- **contract test가 교체 가능성의 유일한 증거입니다.** 새 어댑터는 기존 스위트의 `params`에 추가되며,
  통과하지 못하면 그 어댑터는 존재하지 않는 것으로 취급합니다.
- **워크플로우는 `WorkflowEnvironment`로 검증합니다.** 시간 스킵 덕분에 72시간 승인 타임아웃을 즉시 테스트할 수 있고,
  durability는 "워커를 강제 종료해도 예약이 살아있다"를 실제로 재현해 증명합니다.
- **통합 테스트가 운영 DB를 지우지 않도록 `test_database_url` / `S3_TEST_BUCKET`을 분리**하고 가드를 두었습니다
  (한 번 실제로 데였던 부분입니다).

---

## 13. 설치 및 실행

### 사전 요구사항

- Python 3.12 · [uv](https://docs.astral.sh/uv/)
- Docker (postgres · temporal · minio)
- Playwright 브라우저 (`uv run playwright install chromium`)
- 선택: WeasyPrint 시스템 라이브러리(pango/cairo), Telegram Bot 토큰, `claude login` 또는 `ANTHROPIC_API_KEY`

### 빠른 시작

```bash
# 1. 의존성 + .env + config 생성
make setup

# 2. 인프라 기동 → Temporal UI: localhost:8080, MinIO: localhost:9001
make up

# 3. (REPOSITORY=postgres 인 경우) 마이그레이션
make migrate

# 4. 워커 기동 — 큐마다 별도 프로세스
QUEUE=default make worker
QUEUE=ai      make worker
QUEUE=browser make worker

# 5. API 서버
make api

# 6. Telegram 롱폴링 리스너 (공인 URL 없는 로컬 개발용)
make telegram-listen

# 7. 워크플로우 실패 감시
make watchdog
```

> 인프라는 Docker로, **api/worker는 호스트에서 uv로** 실행합니다. 디버깅 편의를 위한 의도적인 분리입니다.

### 주요 CLI

```bash
uv run python -m auto_apply.cli collect --platforms wanted   # 공고 수집 1회
uv run python -m auto_apply.cli collect-schedule             # cron Schedule 등록 (idempotent)
uv run python -m auto_apply.cli start <application_id> --job-url <URL>   # 지원 워크플로우 시작
uv run python -m auto_apply.cli approve <application_id> --at 2026-08-25T09:00
uv run python -m auto_apply.cli status  <application_id>     # 현재 단계 조회 (Temporal query)
uv run python -m auto_apply.cli cancel  <application_id>

make resume-cleanup                # 플랫폼 고아 첨부파일 정리 (기본 dry-run)
make resume-cleanup ARGS="--yes"   # 실제 삭제
```

### 어댑터 선택 (`.env`)

모든 외부 의존성은 환경변수로 갈아끼웁니다. 기본값은 **인프라 없이 도는 조합**입니다.

| 변수 | 기본값 | 선택지 |
|------|--------|--------|
| `LLM_PROVIDER` | `stub` | `stub` · `anthropic` · `claude_cli` |
| `EXECUTOR` | `replay` | `replay` · `playwright` · `agent_browser` |
| `REPOSITORY` | `file` | `memory` · `file` · `postgres` |
| `STORAGE` | `local` | `memory` · `local` · `s3` |
| `NOTIFIER` | `console` | `console` · `telegram` |
| `JOB_SOURCE` | `fixture` | `fixture` · `live` |
| `PDF_RENDERER` | `weasyprint` | `stub` · `weasyprint` |
| `DRY_RUN_ONLY` | **`true`** | 어떤 경우에도 최종 submit을 하지 않습니다 |

---

## 14. 안전·정책 제약

이 항목들은 기능이 아니라 **설계 제약**이며, 코드 리뷰에서 예외를 두지 않습니다.

| 제약 | 강제 방식 |
|------|-----------|
| **CAPTCHA·추가 인증(SMS 등)을 우회하거나 자동 해결하지 않습니다** | Recipe 실행 중이면 `CaptchaEncountered`를 던지고, 로그인 중이면 `domain/login_flow.detect_login_outcome`이 감지해 실패시킵니다. 둘 다 사람에게 넘깁니다 |
| **새 플랫폼 계정을 대신 만들지 않습니다** | 본인인증이 필요해 사람만 할 수 있습니다. 자동 로그인은 **본인이 이미 가진 계정**의 자격증명만 사용합니다 |
| **최종 submit은 사람 승인 뒤에만** | `DRY_RUN_ONLY=true`가 기본값이며, 사용자 확인 없이 끄지 않습니다 |
| **AI Recipe를 바로 active로 올리지 않습니다** | `draft → candidate → active`, 승격은 사람이 합니다 |
| **플랫폼 rate limit을 우회하지 않습니다** | `platform_policies` + `browser` 큐 동시성 제한 |
| **자격증명이 LLM 프롬프트에 흐르지 않습니다** | Recipe 정책 검증이 `value_literal`의 자격증명 패턴을 차단하고, 개인정보는 `value_ref`로만 주입됩니다 |

`domain/login_flow.py`를 Playwright 글루 코드에서 분리한 것은 순전히 이 이유 때문입니다.
"우회하지 않는다"는 약속의 실제 근거는 문서가 아니라, **실제 사이트 없이 고정 HTML로 검증되는 판정 함수**입니다.

---

## 15. 구현 현황

| 마일스톤 | 상태 | 내용 |
|---------|------|------|
| **M0** | 완료 | 스캐폴딩 · 툴체인 · 계층 가드 · contract test 패턴 |
| **M1** | 완료 | 승인 흐름 · durable timer · Telegram Notifier · nonce · REST 승인 엔드포인트 |
| **M2** | 완료 | Playwright executor · RecipeSource · 실행 모드 분기 · `application_attempts` 감사 로그 · Postgres/Alembic |
| **M3** | 완료 | Fact 기반 이력서 생성 · `ground_check` · 경력/프로젝트 블록 구조 · WeasyPrint PDF · claude CLI 어댑터 · REVISE 3갈래 |
| **M4** | 완료 | `AutomationRepairWorkflow` 전 구간 (LLM diff → 정책 검증 → 샌드박스 dry-run → candidate 저장 → 승격 승인) |
| **부가** | 완료 | 공고 수집 Schedule · watchdog · SUPERVISED 체크포인트 · `/recipes` 엔드포인트 · S3BlobStore · Telegram 채팅 에이전트 |

수집 → 판정 → 이력서 생성 → PDF → 승인 → 예약 → 실행 → 제출 검증까지의 전 구간이
실제 채용 플랫폼(wanted) 계정을 대상으로 end-to-end 검증을 마친 상태입니다.
지원 대상 플랫폼과 실행 엔진은 [§9](#9-ports--adapters--교체-가능성-설계)의 port를 통해 확장합니다.

---

## 더 읽을거리

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 설계 근거 전문(약 1,400줄). 이 README의 각 절이 참조하는 원본입니다
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — 운영 절차
- [`CLAUDE.md`](CLAUDE.md) — 이 저장소에서 코드를 작성할 때 지켜야 하는 규칙과 그 이유
