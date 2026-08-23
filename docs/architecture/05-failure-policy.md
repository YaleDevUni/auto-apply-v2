# 실패 분류와 재시도 정책

> `docs/ARCHITECTURE.md` 색인의 §5. 절 번호는 코드 주석이 참조하므로 바뀌지 않는다.

---

## 5. 실패 분류와 재시도 정책

재시도 정책을 에러 타입으로 결정한다. "일단 3번 재시도"는 V1에서 문제를 숨긴 방식이다.

| 에러 | 성격 | Temporal 처리 |
|---|---|---|
| 네트워크/타임아웃/5xx | 일시적 | 재시도 (exp backoff, 최대 5회) |
| LLM rate limit | 일시적 | 재시도 + backoff 크게 |
| LLM 출력 스키마 위반 | 준일시적 | activity 내부에서 2회 재프롬프트 → 실패 시 non-retryable |
| `RecipeExecutionError` | 구조 변경 | **재시도 금지** → RepairWorkflow |
| `CaptchaEncountered` | 정책 | 재시도 금지 → 사람 |
| `AuthRequired` | 세션 만료 | 재시도 금지 → 사람에게 재로그인 요청 |
| `AlreadySubmitted` | 멱등 충돌 | 성공으로 간주 (verify로 확인) |
| 자격 미달 | 정상 종료 | 재시도 아님, `rejected` |

**`NON_RETRYABLE` 등록 누락은 테스트가 막는다.** 재시도 정책은 예외 *이름 문자열*로
결정되므로(`domain/errors.py`), 새 `TerminalError` 서브클래스를 만들고 `NON_RETRYABLE` 튜플에
넣는 걸 잊으면 아무 에러 없이 조용히 재시도된다 — "재시도해도 결과가 같다"고 클래스 이름으로
선언해놓고 정반대로 동작하는 상태다. `TerminalError`의 모든 서브클래스가 `NON_RETRYABLE`에
있는지를 테스트가 직접 강제한다(`a5f00a6`). 등록을 잊는 실수는 리뷰로 잡기 어렵고(파일 두
군데를 같이 봐야 한다) 증상이 조용해서, 규칙을 문서가 아니라 게이트에 건 사례다.

**부분 제출 위험**: submit 도중 크래시하면 "제출됐는지" 알 수 없다. 그래서
`execute_application`은 submit 직전에 `application_attempts`에 `submitting` 행을 기록하고,
재개 시 항상 `verify_submission`(지원 내역 페이지 확인)을 **먼저** 돌린다. 되돌릴 수 없는
단계 앞뒤에 기록을 남기는 것이 유일한 방어다.

`WantedPlatformAdapter.verify_submission`은 "내 지원 현황" API(`/api/v1/applications`)를
`job_id`로 필터링해 조회한다 — `VerifyInput`에 `job_id`(`JobRef.job_id`)/`since`(이번 시도의
`started_at`)를 추가로 실었다. `since` 없이 job_id만 대조하면 "예전에 같은 공고에 지원한 적
있음"으로 이번 시도와 무관하게 오탐(verified=True)할 수 있어서다 — 거짓 확인이 부분 제출을
놓치는 것보다 위험하다는 원칙(위)의 연장. wanted 서버 시각과 워크플로우 시각(Temporal, UTC)
사이 오차를 흡수하려 5분 여유(`_CLOCK_SKEW`)를 둔다. 이 API는 numeric `user_id`를 요구하는데
storage_state 엔 쿠키만 있어서(`AttachmentManager`와 동일 패턴, `adapters/_wanted_auth.py`)
`/api/v1/me`로 먼저 구한다(agent-browser 라이브 탐색으로 실측, 2026-08-20). `verify_submission`
activity 호출 자체가 실패해도(예: `AuthRequired` — storage_state 만료) `_execution.py`가
잡아서 "확인 안 됨"으로 안전하게 떨어뜨린다 — 안 잡으면 워크플로우가 조용히 FAILED 로 죽는다
(workflow-failure-visibility-backlog 와 같은 이유).
