# auto-apply v2 — Architecture (색인)

> V1의 가장 큰 문제가 "이 지원 건이 지금 어느 단계인지" 추적/디버깅이었다면,
> V2의 설계 목표는 **워크플로우를 1급 객체로 만들고, 그 상태를 우리가 직접 관리하지 않는 것**이다.

이 문서는 **색인**이다. 본문은 `docs/architecture/` 아래로 절 단위로 쪼개져 있다
(2026-08-24 분리 — 한 파일이 1,600줄을 넘어 읽기·수정·리뷰가 모두 불편해졌다).
**절 번호(§N)는 코드 주석·테스트·CLAUDE.md가 직접 참조하므로 파일이 갈려도 바뀌지 않는다.**
새 절을 쓸 때는 아래 표에 줄 하나를 추가한다.

---

## 핵심 불변식 4개

이 4개가 깨지면 V1의 디버깅 지옥이 재현된다.

1. **Temporal = 실행 상태, DB = 비즈니스 데이터.** 진행 단계를 DB에 이중으로 관리하지 않는다.
2. **AI는 제출하지 않는다.** AI는 Recipe(데이터)를 만들고, Playwright는 Recipe를 실행한다.
3. **모든 I/O는 Activity 안에서만.** Workflow 코드는 결정적(deterministic)이어야 한다.
4. **되돌릴 수 없는 행위(최종 submit)는 사람의 승인 뒤에서만.**

---

## 색인

| 절 | 내용 | 파일 |
|---|---|---|
| §0 | 한 줄 요약 — 계층별 책임/비책임 표 | [`00-overview.md`](architecture/00-overview.md) |
| §1 | 시스템 구성도 · Task Queue를 3개로 나누는 이유 | [`00-overview.md`](architecture/00-overview.md) |
| §2 | 워크플로우 5개 · workflow_id = 멱등성 키 | [`02-application-workflow.md`](architecture/02-application-workflow.md) |
| §2.1 | workflow_id 규칙과 dedupe | [`02-application-workflow.md`](architecture/02-application-workflow.md) |
| §2.2 | `ApplicationWorkflow` — 상태 기계 · 승인 · durable timer · REVISE | [`02-application-workflow.md`](architecture/02-application-workflow.md) |
| §2.3 | `ResumeWorkflow` — Fact 기반 생성 · `ground_check` · 블록 구조 · PDF | [`02-3-resume-pipeline.md`](architecture/02-3-resume-pipeline.md) |
| §2.4 | `AutomationRepairWorkflow` — Recipe 자기수선 | [`02-4-repair-and-supervision.md`](architecture/02-4-repair-and-supervision.md) |
| §2.4a | 수선 전 사람 확인(`repair_confirm`) · recipe 격리(quarantine) | [`02-4-repair-and-supervision.md`](architecture/02-4-repair-and-supervision.md) |
| §2.4b | 외부 ATS/자체구축 폼 — `WebAgentExecutor`(Aside, **동결**) | [`02-4-repair-and-supervision.md`](architecture/02-4-repair-and-supervision.md) |
| §2.4c | SUPERVISED 페이지 경계 체크포인트 — `CheckpointWaiter` | [`02-4-repair-and-supervision.md`](architecture/02-4-repair-and-supervision.md) |
| §3 | Browser Automation — Recipe는 코드가 아니라 데이터 · 세션/로그인 · 실행 엔진 2종 | [`03-browser-automation.md`](architecture/03-browser-automation.md) |
| §4 | 데이터 모델 · Temporal과 DB의 경계(§4.1) · S3 레이아웃(§4.2) | [`04-data-model.md`](architecture/04-data-model.md) |
| §5 | 실패 분류와 재시도 정책 · 부분 제출 방어 · `verify_submission` | [`05-failure-policy.md`](architecture/05-failure-policy.md) |
| §6 | Telegram Control Plane — nonce · REVISE · 배지 · 채팅 에이전트 | [`06-telegram.md`](architecture/06-telegram.md) |
| §7 | FastAPI 표면 | [`07-api.md`](architecture/07-api.md) |
| §8 | 리포지토리 구조 | [`08-repo-layout.md`](architecture/08-repo-layout.md) |
| §9 | 열려 있던 결정들 (DB · 오케스트레이션 프레임워크 · 자동 승격 · 관측성 · 정책 리스크) | [`09-decisions.md`](architecture/09-decisions.md) |
| §10 | 마일스톤과 현재 상태 | [`10-milestones.md`](architecture/10-milestones.md) |
| §11.1–11.2b | Ports & Adapters 원칙 · Port 목록 · 공고 수집·매칭 | [`11-ports-and-adapters.md`](architecture/11-ports-and-adapters.md) |
| §11.2c | `ClaudeCodeCliLLM` — API 키 종량제 대신 로컬 구독 | [`11-2-operational-entrypoints.md`](architecture/11-2-operational-entrypoints.md) |
| §11.2d | 워크플로우 능동 감시 `watchdog.py` + 알림 사각지대 4종 | [`11-2-operational-entrypoints.md`](architecture/11-2-operational-entrypoints.md) |
| §11.2e | 플랫폼 첨부파일 정리 — `AttachmentManager` / `resume_cleanup.py` | [`11-2-operational-entrypoints.md`](architecture/11-2-operational-entrypoints.md) |
| §11.2f | 자동 지원 시작 Schedule — `ApplyIntakeWorkflow` + 텔레그램 관리 | [`11-2-operational-entrypoints.md`](architecture/11-2-operational-entrypoints.md) |
| §11.2g | 승인 대기 일괄 재전송 — `pending_decisions.py` | [`11-2-operational-entrypoints.md`](architecture/11-2-operational-entrypoints.md) |
| §11.3–11.7 | activity = seam · composition root · contract test · 추상화 금지 목록 · PR 체크리스트 | [`11-ports-and-adapters.md`](architecture/11-ports-and-adapters.md) |

---

## 어디부터 읽을까

- **처음 보는 사람** → §0 → §1 → §2.2 → §11.1. 여기까지가 "왜 이렇게 생겼는가"다.
- **기능을 추가하려는 사람** → §11.7 체크리스트 → 해당 절 → [`CLAUDE.md`](../CLAUDE.md)의 절대 규칙.
- **운영 중 문제를 보는 사람** → §5(실패 분류) → §11.2d(감시·알림) → [`RUNBOOK.md`](RUNBOOK.md).
- **Recipe를 고치려는 사람** → §3 → §2.4 → [`.claude/agents/recipe-builder.md`](../.claude/agents/recipe-builder.md).

---

## 이 문서를 고치는 규칙

- 설계가 바뀌면 **코드와 같은 커밋에서** 해당 절 파일을 고친다(CLAUDE.md "개발 프로세스").
- 절 번호는 재사용하지 않는다. 절이 사라지면 번호를 비우지 말고 "삭제됨 + 대체 절" 한 줄을 남긴다 —
  코드 주석이 그 번호를 가리키고 있을 수 있다.
- 새 파일을 만들면 위 색인 표에 줄을 추가한다. 색인에 없는 파일은 없는 것과 같다.
