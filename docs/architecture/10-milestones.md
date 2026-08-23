# 마일스톤

> `docs/ARCHITECTURE.md` 색인의 §10. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 10. 마일스톤

| M | 상태 | 목표 | 완료 기준 (Definition of Done) |
|---|---|---|---|
| **M0** | ✅ 완료 | 뼈대 | docker-compose로 postgres·temporal·minio·api·worker 부팅. `ApplicationWorkflow`가 activity 3개를 지나 completed. Temporal UI에서 단계 확인. |
| **M1** | ✅ 완료 | Human-in-the-loop | Telegram 승인 → signal → durable timer → 예약 시각에 깨어남. 워커를 강제 종료해도 예약이 살아있음(`tests/workflows/test_durability.py`). |
| **M2** | ✅ 완료 | 실제 제출 1개 플랫폼 | 손으로 작성한 Recipe(JSON)로 Playwright가 dry-run → supervised → live. `application_attempts`에 감사 로그. Postgres/Alembic. |
| **M3** | ✅ 완료 | AI Resume | Fact 기반 생성 + `ground_check` 게이트. 경력/프로젝트 블록 구조 + WeasyPrint PDF. REVISE 3갈래. **LangGraph 도입은 판단 결과 보류**(§9.2). |
| **M4** | ✅ 완료 | 자기 수선 | `AutomationRepairWorkflow` 전 구간(LLM diff → 정책 검증 → 샌드박스 dry-run → candidate → 사람 승격 → 실행 재개). **OTel 연결은 미완**(§9.4). |
| **M5** | 진행 중 | 다중 플랫폼 | `PlatformAdapter`/`JobSource` 2번째 구현(saramin)까지 왔다. 남은 것: 자소서 문항 있는 공고의 라이브 검증, rate limit·동시성 정책의 실측 검증. |

각 마일스톤의 검증은 "코드가 돌아간다"가 아니라 **"워커를 죽였다 살려도 워크플로우가 이어진다"**로 잡는다.
그게 Temporal을 도입한 유일한 이유이기 때문이다.

### 마일스톤 밖에서 자란 것

M0~M5 축에 안 들어가지만 실제로 구현·검증된 것들. 대부분 라이브 운영 중 발견한 갭에서 나왔다.

| 영역 | 내용 | 절 |
|---|---|---|
| 공고 수집·매칭 | `JobSource` 3종 + 순수 domain 매칭 + `JobCollectionWorkflow` + Schedule(cron) | §11.2b |
| LLM 비용 | `ClaudeCodeCliLLM` — API 키 종량제 대신 로컬 Claude Code 구독 + 프롬프트 캐시 | §11.2c |
| 실패 가시성 | `watchdog.py` 능동 감시 + 알림 사각지대 4종(성공으로 끝나는 실패·프로세스 죽음·감시 실명·인바운드 실패) | §11.2d |
| 계정 위생 | `AttachmentManager` + `resume_cleanup.py` — 지원마다 쌓이는 고아 이력서 파일 정리 | §11.2e |
| 자동 지원 | `ApplyIntakeWorkflow` + Schedule + 텔레그램에서 시각/건수 변경(DB 저장) | §11.2f |
| 승인 복구 | `pending_decisions.py` — 리스너가 죽어 있던 동안의 대기 건 일괄 재전송 | §11.2g |
| 사람 개입 | SUPERVISED 페이지 경계 체크포인트 — `CheckpointWaiter` | §2.4c |
| 대화형 운영 | 텔레그램 자유 텍스트 채팅 에이전트 — 도구 자동 선택 ReAct 루프 | §6 |

### 아직 안 한 것 (의식적으로)

- **OTel exporter 실제 연결** — 자리만 있고 백엔드가 없다. Temporal UI가 1차 콘솔이다(§9.4).
- **`WebAgentExecutor`(외부 ATS) 실행 흐름 배선** — port·adapter·contract test 까지만 있고
  `ApplicationWorkflow`에 안 붙었다. 최후순위로 동결(§2.4b).
- **자소서 답변 생성 파이프라인** — 이력서는 되지만 자유 서술형 문항은 아직 없다.
- **`ResumeReviewer`의 2번째 구현** — §11.1 "구현 2개" 원칙의 유일한 미충족 지점.
  LLM 2차 리뷰를 일부러 안 넣기로 한 결정과 묶여 있다(§2.3).
